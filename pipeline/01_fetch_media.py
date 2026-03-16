"""
pipeline/01_fetch_media.py
--------------------------
Fetches daily media signals for each CCP member using the GDELT DOC 2.0 API.
Writes results to data/media_history.json as a rolling time series.

What it does:
  - For each member in data/members.json:
      1. Fetches daily mention counts via GDELT timelinevolraw (no 250-article cap)
      2. Fetches Xi co-occurrence counts (same method)
      3. Fetches up to 5 recent article titles for position scoring
  - Appends today's data point to each member's series in media_history.json
  - Prunes entries older than RETENTION_DAYS to keep file size manageable

Run:  python pipeline/01_fetch_media.py
"""

import json
import time
import datetime
import requests
from pathlib import Path

# ── PATHS ─────────────────────────────────────────────────────────────────────

ROOT         = Path(__file__).parent.parent
MEMBERS_FILE = ROOT / "data" / "members.json"
HISTORY_FILE = ROOT / "data" / "media_history.json"

# ── CONFIG ────────────────────────────────────────────────────────────────────

RETENTION_DAYS    = 180
REQUEST_DELAY     = 1.5
POSITION_ARTICLES = 5
LOOKBACK_DAYS     = 7
GDELT_DOC_URL     = "https://api.gdeltproject.org/api/v2/doc/doc"
XI_NAME           = "Xi Jinping"
SOURCES           = ["xinhuanet.com", "en.people.cn"]

# ── GDELT REQUEST ─────────────────────────────────────────────────────────────

def gdelt_request(params: dict) -> dict:
    """Make a GDELT DOC API request with exponential backoff. Returns {} on failure."""
    wait = 10
    for attempt in range(3):
        try:
            r = requests.get(GDELT_DOC_URL, params=params, timeout=15)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 503):
                print(f"      {r.status_code} — waiting {wait}s (attempt {attempt+1}/3)...")
                time.sleep(wait)
                wait *= 2
                continue
            print(f"      GDELT {r.status_code} for [{params.get('query','')[:50]}]")
            return {}
        except requests.exceptions.Timeout:
            print(f"      GDELT timeout — waiting {wait}s (attempt {attempt+1}/3)...")
            time.sleep(wait)
            wait *= 2
        except Exception as e:
            print(f"      GDELT error: {e}")
            return {}
    return {}


# ── FETCH FUNCTIONS ───────────────────────────────────────────────────────────

def source_filter() -> str:
    return " OR ".join(f"domain:{s}" for s in SOURCES)


def fetch_daily_counts(name: str, date_from: str, date_to: str) -> dict:
    """
    Fetch daily mention counts using timelinevolraw.
    Returns {date_str: count} — no 250 cap, true daily counts.
    """
    params = {
        "query":          f'"{name}" ({source_filter()})',
        "mode":           "timelinevolraw",
        "format":         "json",
        "startdatetime":  date_from.replace("-", "") + "000000",
        "enddatetime":    date_to.replace("-", "") + "235959",
        "timelinesmooth": 0,
    }
    data   = gdelt_request(params)
    time.sleep(REQUEST_DELAY)
    counts = {}
    for series in data.get("timeline", []):
        for point in series.get("data", []):
            d = point.get("date", "")[:10]
            c = int(point.get("value", 0))
            if d:
                counts[d] = counts.get(d, 0) + c
    return counts


def fetch_xi_cooccurrence_counts(name: str, date_from: str, date_to: str) -> dict:
    """
    Fetch daily counts for articles mentioning both name AND Xi Jinping.
    Returns {date_str: count}.
    """
    params = {
        "query":          f'"{XI_NAME}" "{name}" ({source_filter()})',
        "mode":           "timelinevolraw",
        "format":         "json",
        "startdatetime":  date_from.replace("-", "") + "000000",
        "enddatetime":    date_to.replace("-", "") + "235959",
        "timelinesmooth": 0,
    }
    data   = gdelt_request(params)
    time.sleep(REQUEST_DELAY)
    counts = {}
    for series in data.get("timeline", []):
        for point in series.get("data", []):
            d = point.get("date", "")[:10]
            c = int(point.get("value", 0))
            if d:
                counts[d] = counts.get(d, 0) + c
    return counts


def fetch_recent_articles(name: str, date_from: str) -> list:
    """
    Fetch recent article titles and URLs for position scoring.
    Returns list of {title, url, date}.
    """
    params = {
        "query":          f'"{name}" ({source_filter()})',
        "mode":           "artlist",
        "format":         "json",
        "maxrecords":     POSITION_ARTICLES,
        "startdatetime":  date_from.replace("-", "") + "000000",
        "enddatetime":    datetime.date.today().strftime("%Y%m%d") + "235959",
        "sort":           "datedesc",
    }
    data     = gdelt_request(params)
    time.sleep(REQUEST_DELAY)
    articles = []
    for item in data.get("articles", []):
        articles.append({
            "title": item.get("title", ""),
            "url":   item.get("url", ""),
            "date":  item.get("seendate", "")[:10],
        })
    return articles


# ── HISTORY I/O ───────────────────────────────────────────────────────────────

def load_history() -> dict:
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"schema_version": 1, "retention_days": RETENTION_DAYS,
            "last_updated": None, "series": {}}


def save_history(history: dict):
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


def prune_history(history: dict) -> dict:
    cutoff = (datetime.date.today() - datetime.timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%d")
    for mid in history["series"]:
        history["series"][mid] = [e for e in history["series"][mid] if e.get("date","") >= cutoff]
    return history


# ── LOAD MEMBERS ──────────────────────────────────────────────────────────────

def load_members() -> list:
    with open(MEMBERS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    members = []
    for m in data.get("members", []):
        members.append({
            "id":            m["id"],
            "name_en":       m["name_en"],
            "name_variants": m.get("name_variants", [m["name_en"]]),
            "tier":          m["tier"],
        })
    for m in data.get("cc_members_stub", {}).get("members", []):
        members.append({
            "id":            m["id"],
            "name_en":       m["name_en"],
            "name_variants": [m["name_en"]],
            "tier":          "cc",
        })
    return members


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    today     = datetime.date.today()
    date_to   = today.strftime("%Y-%m-%d")
    date_from = (today - datetime.timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    today_str = today.strftime("%Y-%m-%d")

    print(f"01_fetch_media.py — {datetime.datetime.now().isoformat()}")
    print(f"Window: {date_from} to {date_to} | Retention: {RETENTION_DAYS} days\n")

    members = load_members()
    history = load_history()

    print(f"Members: {len(members)} | Existing series: {len(history['series'])}\n")

    for i, member in enumerate(members):
        name      = member["name_en"]
        mid       = member["id"]
        tier      = member["tier"]
        print(f"  [{i+1:>3}/{len(members)}] [{tier.upper()}] {name}...")

        # Use primary name variant only for efficiency
        primary = member["name_variants"][0]
        mention_counts = fetch_daily_counts(primary, date_from, date_to)
        xi_counts      = fetch_xi_cooccurrence_counts(primary, date_from, date_to)
        articles       = fetch_recent_articles(primary, date_from)

        total_mentions = sum(mention_counts.values())
        total_xi       = sum(xi_counts.values())
        last_seen      = next(
            (d for d in sorted(mention_counts.keys(), reverse=True) if mention_counts[d] > 0),
            "Unknown"
        )

        print(f"      mentions: {total_mentions} | xi: {total_xi} | "
              f"last seen: {last_seen} | articles: {len(articles)}")

        data_point = {
            "date":            today_str,
            "window_start":    date_from,
            "window_end":      date_to,
            "mentions":        total_mentions,
            "xi_cooccurrence": total_xi,
            "daily_mentions":  mention_counts,
            "daily_xi":        xi_counts,
            "last_seen":       last_seen,
            "recent_articles": articles,
        }

        # Init series if needed
        if mid not in history["series"]:
            history["series"][mid] = []

        # Replace today's entry if rerunning, else append
        history["series"][mid] = [e for e in history["series"][mid] if e["date"] != today_str]
        history["series"][mid].append(data_point)

    history = prune_history(history)
    history["last_updated"] = today_str
    save_history(history)

    max_depth = max((len(s) for s in history["series"].values() if s), default=0)
    print(f"\nDone. {HISTORY_FILE}")
    print(f"Series: {len(history['series'])} members | Max depth: {max_depth} days")


if __name__ == "__main__":
    main()
