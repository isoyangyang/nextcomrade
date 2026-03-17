"""
pipeline/02_fetch_chinese_rss.py
---------------------------------
Fetches Chinese-language RSS feeds from People's Daily and Xinhua.
Counts member name mentions, extracts dates, and appends to
data/chinese_rss_history.json as a rolling time series.

Sources:
  - People's Daily Politics: http://www.people.com.cn/rss/politics.xml
    100 items, updated daily, reliable UTF-8 content, strong signal
  - Xinhua Politics: http://www.xinhuanet.com/politics/news_politics.xml
    300 items, Chinese text, supplementary signal

Why Chinese RSS matters:
  - 3-5x more articles than English editions
  - No API limits, no rate limiting, no authentication
  - Covers People's Daily which GDELT English misses entirely
  - Name disambiguation: 李强 is unambiguous, "Li Qiang" is not

Run: python pipeline/02_fetch_chinese_rss.py
"""

import json
import time
import datetime
import re
import warnings
import requests
from pathlib import Path
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# ── PATHS ─────────────────────────────────────────────────────────────────────

ROOT         = Path(__file__).parent.parent
MEMBERS_FILE = ROOT / "data" / "members.json"
HISTORY_FILE = ROOT / "data" / "chinese_rss_history.json"

# ── CONFIG ────────────────────────────────────────────────────────────────────

RETENTION_DAYS = 180
REQUEST_DELAY  = 3.0    # seconds between feed fetches

FEEDS = [
    {
        "id":       "peoples_daily_politics",
        "name":     "People's Daily Politics",
        "url":      "http://www.people.com.cn/rss/politics.xml",
        "weight":   1.5,    # higher weight — more authoritative signal
        "encoding": "utf-8",
    },
    {
        "id":       "xinhua_politics",
        "name":     "Xinhua Politics",
        "url":      "http://www.xinhuanet.com/politics/news_politics.xml",
        "weight":   1.0,
        "encoding": "utf-8",
    },
]

HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/122.0.0.0 Safari/537.36",
    "Accept":          "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept-Charset":  "utf-8",
}

# ── FETCH FEED ────────────────────────────────────────────────────────────────

def fetch_feed(feed: dict) -> list:
    """
    Fetch an RSS feed and return list of items as dicts.
    Each item: {title, link, date, description, raw_text}
    Returns [] on failure.
    """
    try:
        r = requests.get(feed["url"], headers=HEADERS, timeout=15)
        r.raise_for_status()
        r.encoding = feed.get("encoding", "utf-8")
        content    = r.text
        time.sleep(REQUEST_DELAY)
    except Exception as e:
        print(f"    WARNING: feed fetch failed [{feed['name']}]: {e}")
        return []

    soup  = BeautifulSoup(content, "html.parser")
    items = soup.find_all("item") or soup.find_all("entry")

    result = []
    for item in items:
        title = item.find("title")
        link  = item.find("link")
        date  = (item.find("pubdate") or item.find("published")
                 or item.find("updated") or item.find("dc:date"))
        desc  = item.find("description") or item.find("summary")

        t = _clean_cdata(title.get_text(strip=True)) if title else ""
        l = _clean_cdata(link.get_text(strip=True))  if link  else ""
        d = _parse_date(date.get_text(strip=True))   if date  else None
        s = _clean_cdata(desc.get_text(strip=True))  if desc  else ""

        result.append({
            "title":       t,
            "link":        l,
            "date":        d,
            "description": s[:200],
            "raw_text":    (t + " " + s)[:500],
        })

    return result


def _clean_cdata(text: str) -> str:
    """Strip CDATA wrappers and whitespace."""
    return re.sub(r'<!\[CDATA\[|\]\]>', '', text).strip()


def _parse_date(date_str: str) -> str | None:
    """
    Parse various date formats to YYYY-MM-DD string.
    Returns None if unparseable.
    """
    if not date_str:
        return None
    date_str = date_str.strip()

    # Already YYYY-MM-DD
    m = re.search(r'(\d{4}-\d{2}-\d{2})', date_str)
    if m:
        return m.group(1)

    # RFC 822: "Thu, 05 Jun 2025 10:00:00 +0800"
    try:
        from email.utils import parsedate
        t = parsedate(date_str)
        if t:
            return f"{t[0]:04d}-{t[1]:02d}-{t[2]:02d}"
    except Exception:
        pass

    return None


# ── COUNT MENTIONS ────────────────────────────────────────────────────────────

def count_mentions(items: list, name_zh: str) -> dict:
    """
    Count how many RSS items mention name_zh, broken down by date.
    Returns {date_str: count} and also list of matching titles.
    """
    date_counts  = {}
    sample_titles = []

    for item in items:
        text = item["raw_text"]
        if name_zh in text:
            d = item.get("date") or "unknown"
            date_counts[d] = date_counts.get(d, 0) + 1
            if len(sample_titles) < 3:
                sample_titles.append(item["title"][:60])

    return date_counts, sample_titles


def count_xi_cooccurrence(items: list, name_zh: str) -> int:
    """Count items mentioning both name_zh AND Xi Jinping (习近平)."""
    xi = "习近平"
    return sum(1 for item in items if name_zh in item["raw_text"] and xi in item["raw_text"])


# ── HISTORY I/O ───────────────────────────────────────────────────────────────

def load_history() -> dict:
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {
        "schema_version": 1,
        "retention_days": RETENTION_DAYS,
        "last_updated":   None,
        "series":         {},
    }


def save_history(history: dict):
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


def prune_history(history: dict) -> dict:
    cutoff = (datetime.date.today() - datetime.timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%d")
    for mid in history["series"]:
        history["series"][mid] = [
            e for e in history["series"][mid]
            if e.get("date", "") >= cutoff
        ]
    return history


# ── LOAD MEMBERS ──────────────────────────────────────────────────────────────

def load_members() -> list:
    with open(MEMBERS_FILE, encoding="utf-8") as f:
        data = json.load(f)

    members = []
    for m in data.get("members", []):
        name_zh = m.get("name_zh", "")
        if name_zh:
            members.append({
                "id":      m["id"],
                "name_en": m["name_en"],
                "name_zh": name_zh,
                "tier":    m["tier"],
            })

    # Stub CC members — use name_zh if available
    for m in data.get("cc_members_stub", {}).get("members", []):
        name_zh = m.get("name_zh", "")
        if name_zh:
            members.append({
                "id":      m["id"],
                "name_en": m["name_en"],
                "name_zh": name_zh,
                "tier":    "cc",
            })

    return members


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    today     = datetime.date.today()
    today_str = today.strftime("%Y-%m-%d")

    print(f"02_fetch_chinese_rss.py — {datetime.datetime.now().isoformat()}")
    print(f"Date: {today_str} | Feeds: {len(FEEDS)}\n")

    # Fetch all feeds once
    print("Fetching RSS feeds...")
    all_items = []
    for feed in FEEDS:
        print(f"  {feed['name']}...")
        items = fetch_feed(feed)
        # Tag each item with feed weight for scoring
        for item in items:
            item["feed_weight"] = feed["weight"]
            item["feed_id"]     = feed["id"]
        all_items.extend(items)
        print(f"    {len(items)} items fetched")

    total_items = len(all_items)
    print(f"\nTotal items: {total_items}")

    if total_items == 0:
        print("No items fetched — aborting")
        return

    # Load data
    members = load_members()
    history = load_history()

    print(f"Members with Chinese names: {len(members)}\n")

    # Count mentions for each member
    print("Counting mentions...")
    for member in members:
        mid     = member["id"]
        name_zh = member["name_zh"]
        name_en = member["name_en"]
        tier    = member["tier"]

        date_counts, sample_titles = count_mentions(all_items, name_zh)
        xi_count = count_xi_cooccurrence(all_items, name_zh)
        total    = sum(date_counts.values())

        if total > 0:
            print(f"  [{tier.upper()}] {name_en} ({name_zh}): "
                  f"{total} mentions, {xi_count} w/Xi")
            if sample_titles:
                for t in sample_titles[:1]:
                    print(f"    → {t}")

        # Last seen date from RSS items
        dated_items = [d for d in date_counts.keys() if d and d != "unknown"]
        last_seen   = max(dated_items) if dated_items else "Unknown"

        data_point = {
            "date":            today_str,
            "mentions":        total,
            "xi_cooccurrence": xi_count,
            "date_breakdown":  date_counts,
            "last_seen":       last_seen,
            "sample_titles":   sample_titles[:3],
            "feed_item_count": total_items,
        }

        if mid not in history["series"]:
            history["series"][mid] = []

        # Replace today's entry if rerunning
        history["series"][mid] = [
            e for e in history["series"][mid]
            if e["date"] != today_str
        ]
        history["series"][mid].append(data_point)

    history = prune_history(history)
    history["last_updated"] = today_str
    save_history(history)

    # Summary
    active = sum(
        1 for mid, series in history["series"].items()
        if series and series[-1].get("mentions", 0) > 0
    )
    print(f"\nDone. {HISTORY_FILE}")
    print(f"Members tracked: {len(history['series'])} | "
          f"With mentions today: {active}")


if __name__ == "__main__":
    main()
