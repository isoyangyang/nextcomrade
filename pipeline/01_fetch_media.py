"""
pipeline/01_fetch_media.py
--------------------------
Fetches daily media signals for each CCP member using the GDELT DOC 2.0 API.
Writes results to data/media_history.json as a rolling time series.

Optimised for GDELT rate limits:
  - Daily run: mention counts only (1 request per member = 49 requests total)
  - Xi co-occurrence and article fetches run on alternating days to spread load
  - Exponential backoff on 429s starting at 30s

Run: python pipeline/01_fetch_media.py
"""

import json
import time
import datetime
import requests
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
from fetch_stealth import StealthSession, shuffled_members, make_session

# ── PATHS ─────────────────────────────────────────────────────────────────────

ROOT         = Path(__file__).parent.parent
MEMBERS_FILE = ROOT / "data" / "members.json"
HISTORY_FILE = ROOT / "data" / "media_history.json"

# ── CONFIG ────────────────────────────────────────────────────────────────────

RETENTION_DAYS    = 180
REQUEST_DELAY     = 4.0   # seconds between requests
POSITION_ARTICLES = 5
LOOKBACK_DAYS     = 7
GDELT_DOC_URL     = "https://api.gdeltproject.org/api/v2/doc/doc"
XI_NAME           = "Xi Jinping"
SOURCES           = ["xinhuanet.com", "en.people.cn"]

# Stagger heavy requests across days to avoid rate limits
# Day 0 (Mon/Thu): mentions only
# Day 1 (Tue/Fri): mentions + xi cooccurrence
# Day 2 (Wed/Sat): mentions + articles (top 20 only)
# Day 3 (Sun):     all (weekly full refresh)
TODAY_DOW = datetime.date.today().weekday()   # 0=Mon, 6=Sun
FETCH_XI       = TODAY_DOW in (1, 4, 6)       # Tue, Fri, Sun
FETCH_ARTICLES = TODAY_DOW in (2, 5, 6)       # Wed, Sat, Sun
ARTICLE_TOP_N  = 20                            # only fetch articles for top N by tier

# ── GDELT REQUEST ─────────────────────────────────────────────────────────────

def gdelt_request(session: StealthSession, params: dict) -> dict:
    """GDELT DOC API request via stealth session. Returns {} on failure."""
    wait = 30
    for attempt in range(3):
        try:
            r = session.session.get(
                GDELT_DOC_URL,
                params=params,
                headers={
                    "User-Agent":     session.ua,
                    "Accept":         "application/json, text/plain, */*",
                    "Referer":        "https://www.gdeltproject.org/",
                    "Sec-Fetch-Mode": "cors",
                    "Sec-Fetch-Dest": "empty",
                    "Sec-Fetch-Site": "cross-site",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=20,
            )
            if r.status_code == 200:
                time.sleep(REQUEST_DELAY)
                return r.json()
            if r.status_code in (429, 503):
                print(f"      {r.status_code} — waiting {wait}s (attempt {attempt+1}/3)...")
                time.sleep(wait)
                wait *= 2
                continue
            if r.status_code == 400:
                print(f"      400 bad request — skipping")
                return {}
            print(f"      GDELT {r.status_code}")
            return {}
        except requests.exceptions.Timeout:
            print(f"      timeout — waiting {wait}s (attempt {attempt+1}/3)...")
            time.sleep(wait)
            wait *= 2
        except Exception as e:
            print(f"      error: {e}")
            return {}
    print(f"      gave up after 3 attempts")
    return {}


# ── FETCH FUNCTIONS ───────────────────────────────────────────────────────────

def sf() -> str:
    return " OR ".join(f"domain:{s}" for s in SOURCES)


def fetch_daily_counts(session: StealthSession, name: str, date_from: str, date_to: str) -> dict:
    """Mention counts via timelinevolraw. Returns {date_str: count}."""
    params = {
        "query":          f'"{name}" ({sf()})',
        "mode":           "timelinevolraw",
        "format":         "json",
        "startdatetime":  date_from.replace("-", "") + "000000",
        "enddatetime":    date_to.replace("-", "") + "235959",
        "timelinesmooth": 0,
    }
    data   = gdelt_request(session, params)
    counts = {}
    for series in data.get("timeline", []):
        for point in series.get("data", []):
            d = point.get("date", "")[:10]
            c = int(point.get("value", 0))
            if d:
                counts[d] = counts.get(d, 0) + c
    return counts


def fetch_xi_counts(session: StealthSession, name: str, date_from: str, date_to: str) -> dict:
    """Articles mentioning both name AND Xi Jinping. Returns {date_str: count}."""
    params = {
        "query":          f'"{XI_NAME}" "{name}" ({sf()})',
        "mode":           "timelinevolraw",
        "format":         "json",
        "startdatetime":  date_from.replace("-", "") + "000000",
        "enddatetime":    date_to.replace("-", "") + "235959",
        "timelinesmooth": 0,
    }
    data   = gdelt_request(session, params)
    counts = {}
    for series in data.get("timeline", []):
        for point in series.get("data", []):
            d = point.get("date", "")[:10]
            c = int(point.get("value", 0))
            if d:
                counts[d] = counts.get(d, 0) + c
    return counts


def fetch_articles(session: StealthSession, name: str, date_from: str) -> list:
    """Recent article titles and URLs for position scoring."""
    params = {
        "query":          f'"{name}" ({sf()})',
        "mode":           "artlist",
        "format":         "json",
        "maxrecords":     POSITION_ARTICLES,
        "startdatetime":  date_from.replace("-", "") + "000000",
        "enddatetime":    datetime.date.today().strftime("%Y%m%d") + "235959",
        "sort":           "datedesc",
    }
    data = gdelt_request(session, params)
    return [
        {"title": a.get("title",""), "url": a.get("url",""), "date": a.get("seendate","")[:10]}
        for a in data.get("articles", [])
    ]


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
        history["series"][mid] = [
            e for e in history["series"][mid] if e.get("date","") >= cutoff
        ]
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
    print(f"Window: {date_from} to {date_to} | DOW: {TODAY_DOW}")
    print(f"Fetching: mentions=YES  xi={FETCH_XI}  articles={FETCH_ARTICLES}\n")

    members = load_members()
    history = load_history()

    # Shuffle member order daily — avoids detectable sequential pattern
    members = shuffled_members(members)

    psc_pb_ids = {m["id"] for m in members if m["tier"] in ("psc", "pb")}

    print(f"Members: {len(members)} | Existing series: {len(history['series'])}")
    print(f"Processing order randomised for today\n")

    # Create stealth session — warm up on GDELT homepage
    session = make_session("https://www.gdeltproject.org/", base_delay=REQUEST_DELAY)

    for i, member in enumerate(members):
        name  = member["name_en"]
        mid   = member["id"]
        tier  = member["tier"]
        print(f"  [{i+1:>3}/{len(members)}] [{tier.upper()}] {name}...")

        primary = member["name_variants"][0]

        # Always fetch mention counts
        mention_counts = fetch_daily_counts(session, primary, date_from, date_to)

        # Xi co-occurrence — staggered
        xi_counts = {}
        if FETCH_XI:
            xi_counts = fetch_xi_counts(session, primary, date_from, date_to)

        # Articles — PSC/PB on article days only
        articles = []
        if FETCH_ARTICLES and mid in psc_pb_ids:
            articles = fetch_articles(session, primary, date_from)

        total_mentions = sum(mention_counts.values())
        total_xi       = sum(xi_counts.values())
        last_seen      = next(
            (d for d in sorted(mention_counts.keys(), reverse=True)
             if mention_counts[d] > 0),
            "Unknown"
        )

        print(f"      mentions: {total_mentions} | xi: {total_xi} | "
              f"last seen: {last_seen} | articles: {len(articles)}")

        # Carry forward xi/articles if not fetching today
        prev = next(
            (e for e in reversed(history.get("series", {}).get(mid, []))
             if e.get("date") != today_str),
            {}
        )
        if not FETCH_XI:
            xi_counts = prev.get("daily_xi", {})
            total_xi  = prev.get("xi_cooccurrence", 0)
        if not (FETCH_ARTICLES and mid in psc_pb_ids):
            articles = prev.get("recent_articles", [])

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

        if mid not in history["series"]:
            history["series"][mid] = []

        history["series"][mid] = [
            e for e in history["series"][mid] if e["date"] != today_str
        ]
        history["series"][mid].append(data_point)

    history = prune_history(history)
    history["last_updated"] = today_str
    save_history(history)

    max_depth = max((len(s) for s in history["series"].values() if s), default=0)
    print(f"\nDone. {HISTORY_FILE}")
    print(f"Series: {len(history['series'])} | Max depth: {max_depth} days")


if __name__ == "__main__":
    main()
