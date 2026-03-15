"""
scrape_scores.py
----------------
Fetches Xinhua mention counts for each CCP member over the last 30 days,
computes a weighted score, and writes scores.json to be consumed by the frontend.

Run locally:  python scrape_scores.py
Run via CI:   see .github/workflows/update_scores.yml

Dependencies: pip install requests beautifulsoup4
"""

import json
import time
import datetime
import re
from collections import defaultdict
import requests
from bs4 import BeautifulSoup

# ── CONFIG ────────────────────────────────────────────────────────────────────

LOOKBACK_DAYS = 30          # how far back to search
RECENCY_HALFLIFE = 7        # days — mentions this week count 2x vs 2 weeks ago
REQUEST_DELAY = 2.0         # seconds between requests — be polite to Xinhua
OUTPUT_FILE = "scores.json"

# ── MEMBERS ───────────────────────────────────────────────────────────────────
# Each entry: (name_for_search, tier, display_role)
# Name variants are listed as a tuple — counts are summed across all variants.

MEMBERS = [
    # PSC
    (("Li Qiang",),               "psc", "Premier, State Council"),
    (("Zhao Leji",),               "psc", "Chairman, NPC"),
    (("Wang Huning",),             "psc", "Chairman, CPPCC"),
    (("Cai Qi",),                  "psc", "Director, General Office"),
    (("Ding Xuexiang",),           "psc", "Executive Vice Premier"),
    (("Li Xi",),                   "psc", "Secretary, CCDI"),
    (("Han Zheng",),               "psc", "Vice President (State)"),
    # Politburo
    (("Chen Wenqing",),            "pb",  "Director, Political-Legal Comm."),
    (("Zhang Youxia", "Zhang You-xia"), "pb", "Vice Chairman, CMC"),
    (("He Weidong",),              "pb",  "Vice Chairman, CMC"),
    (("Liu Guozhong",),            "pb",  "Vice Premier"),
    (("Ma Xingrui",),              "pb",  "Party Secretary, Xinjiang"),
    (("Yuan Jiajun",),             "pb",  "Party Secretary, Chongqing"),
    (("Li Ganjie",),               "pb",  "Party Secretary, Guangdong"),
    (("Shen Yiqin",),              "pb",  "Executive Vice Premier"),
    (("Zhang Guoqing",),           "pb",  "Vice Premier"),
    (("Wang Yi",),                 "pb",  "Director, Foreign Affairs Comm."),
    (("Li Hongzhong",),            "pb",  "Deputy Chair, NPC"),
    (("Chen Min'er", "Chen Miner"),"pb",  "Party Secretary, Tianjin"),
    (("Yin Li",),                  "pb",  "Party Secretary, Shanghai"),
    (("He Lifeng",),               "pb",  "Vice Premier"),
    (("Liu Jinguo",),              "pb",  "Deputy Secretary, CCDI"),
    (("Guo Shengkun",),            "pb",  "Deputy Chair, NPC"),
    (("Huang Kunming",),           "pb",  "Former Party Secretary, Guangdong"),
    (("Dong Jun",),                "pb",  "Minister of National Defense"),
    # Central Committee (selected — add more as needed)
    (("Chen Quanguo",),            "cc",  "Central Committee Member"),
    (("Hu Chunhua",),              "cc",  "Central Committee Member"),
    (("Li Shulei",),               "cc",  "Central Committee Member"),
    (("Qin Gang",),                "cc",  "Central Committee Member"),
    (("Chen Xi",),                 "cc",  "Central Committee Member"),
    (("Bai Chunli",),              "cc",  "Central Committee Member"),
    (("Cao Chunting",),            "cc",  "Central Committee Member"),
    (("Chen Guangguo",),           "cc",  "Central Committee Member"),
    (("Chen Jining",),             "cc",  "Central Committee Member"),
    (("Dong Jun",),                "cc",  "Central Committee Member"),
    (("Fan Changlong",),           "cc",  "Central Committee Member"),
    (("Fu Zhenghua",),             "cc",  "Central Committee Member"),
    (("Gao Jin",),                 "cc",  "Central Committee Member"),
    (("Han Changfu",),             "cc",  "Central Committee Member"),
    (("Hu Heping",),               "cc",  "Central Committee Member"),
    (("Jin Zhuanglong",),          "cc",  "Central Committee Member"),
    (("Li Yunze",),                "cc",  "Central Committee Member"),
    (("Liu He",),                  "cc",  "Central Committee Member"),
    (("Liu Zhenli",),              "cc",  "Central Committee Member"),
    (("Luo Huining",),             "cc",  "Central Committee Member"),
    (("Ma Wenrui",),               "cc",  "Central Committee Member"),
    (("Ni Yuefeng",),              "cc",  "Central Committee Member"),
    (("Ning Jizhe",),              "cc",  "Central Committee Member"),
    (("Pan Gongshen",),            "cc",  "Central Committee Member"),
    (("Shi Taifeng",),             "cc",  "Central Committee Member"),
    (("Sun Chunlan",),             "cc",  "Central Committee Member"),
    (("Tang Yijun",),              "cc",  "Central Committee Member"),
    (("Wang Xiaohong",),           "cc",  "Central Committee Member"),
    (("Wei Fenghe",),              "cc",  "Central Committee Member"),
    (("Xia Baolong",),             "cc",  "Central Committee Member"),
    (("Xu Qiliang",),              "cc",  "Central Committee Member"),
    (("Yang Xiaodu",),             "cc",  "Central Committee Member"),
    (("Zhang Jun",),               "cc",  "Central Committee Member"),
    (("Zhao Kezhi",),              "cc",  "Central Committee Member"),
    (("Zhou Qiang",),              "cc",  "Central Committee Member"),
]

# ── XINHUA SEARCH ─────────────────────────────────────────────────────────────

XINHUA_SEARCH = "https://so.news.cn/en/query"

def fetch_mention_count(name: str, days_ago_start: int, days_ago_end: int = 0) -> int:
    """
    Search Xinhua English for `name` within a date window.
    Returns result count (capped by what Xinhua reports).
    """
    today = datetime.date.today()
    date_from = (today - datetime.timedelta(days=days_ago_start)).strftime("%Y-%m-%d")
    date_to   = (today - datetime.timedelta(days=days_ago_end)).strftime("%Y-%m-%d")

    params = {
        "keyword": f'"{name}"',
        "lang": "en",
        "sortField": "score",
        "dateFrom": date_from,
        "dateTo": date_to,
        "curPage": 1,
        "pageSize": 10,
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://so.news.cn/",
    }

    try:
        r = requests.get(XINHUA_SEARCH, params=params, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        # Xinhua returns total in data.data.total or similar — adjust if structure changes
        total = (
            data.get("data", {}).get("total", 0)
            or data.get("total", 0)
            or 0
        )
        return int(total)
    except Exception as e:
        print(f"    WARNING: fetch failed for '{name}': {e}")
        return 0


def weighted_score(name_variants: tuple) -> tuple[float, int, str]:
    """
    Compute a recency-weighted mention score across LOOKBACK_DAYS.
    Splits the window into weekly buckets and applies exponential decay.
    Returns (weighted_score, raw_total, last_seen_date_str).
    """
    buckets = []
    # Break lookback into weekly windows
    weeks = LOOKBACK_DAYS // 7
    for w in range(weeks):
        days_start = LOOKBACK_DAYS - (w * 7)
        days_end   = days_start - 7
        bucket_count = 0
        for name in name_variants:
            bucket_count += fetch_mention_count(name, days_start, days_end)
            time.sleep(REQUEST_DELAY)
        # weight: most recent week = 1.0, each older week decays by half-life
        weight = 2 ** (w * -7 / RECENCY_HALFLIFE)  # e.g. week 0=1.0, week1≈0.5, week2≈0.25
        buckets.append((bucket_count, weight))

    raw_total = sum(c for c, _ in buckets)
    score = sum(c * w for c, w in buckets)

    # Last seen: find most recent non-zero week
    last_seen = "Unknown"
    today = datetime.date.today()
    for w, (count, _) in enumerate(buckets):
        if count > 0:
            approx_date = today - datetime.timedelta(days=w*7)
            last_seen = approx_date.strftime("%Y-%m-%d")
            break

    return score, raw_total, last_seen


# ── TIER FLOOR WEIGHTS ────────────────────────────────────────────────────────
# Even if someone has zero Xinhua mentions (rare for PSC), we apply a
# minimum floor so they don't drop to 0%. This preserves the hierarchy.

TIER_FLOOR = {
    "psc": 5.0,   # PSC members always have some baseline weight
    "pb":  1.0,
    "cc":  0.1,
}

# Wang Huning override — fixed regardless of media score
FIXED_SCORES = {
    "Wang Huning": 0.08,
}

# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print(f"Starting scrape — {datetime.datetime.now().isoformat()}")
    print(f"Lookback: {LOOKBACK_DAYS} days, {len(MEMBERS)} members\n")

    results = []

    for name_variants, tier, role in MEMBERS:
        primary_name = name_variants[0]
        print(f"  [{tier.upper()}] {primary_name}...")

        # Fixed override
        if primary_name in FIXED_SCORES:
            results.append({
                "name": primary_name,
                "tier": tier,
                "role": role,
                "raw_score": FIXED_SCORES[primary_name],
                "mention_count": 0,
                "last_seen": "N/A",
                "fixed": True,
            })
            print(f"    → fixed at {FIXED_SCORES[primary_name]}")
            continue

        score, count, last_seen = weighted_score(name_variants)
        print(f"    → mentions: {count}, weighted score: {score:.2f}, last seen: {last_seen}")

        # Apply floor so hierarchy is respected even for quiet members
        floor = TIER_FLOOR[tier]
        final_score = max(score, floor)

        results.append({
            "name": primary_name,
            "tier": tier,
            "role": role,
            "raw_score": final_score,
            "mention_count": count,
            "last_seen": last_seen,
            "fixed": False,
        })

    # Normalise to percentages
    total = sum(r["raw_score"] for r in results)
    for r in results:
        r["probability"] = round(r["raw_score"] / total * 100, 4)

    # Sort by probability descending
    results.sort(key=lambda r: r["probability"], reverse=True)
    for i, r in enumerate(results):
        r["rank"] = i + 1

    # Write output
    output = {
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "lookback_days": LOOKBACK_DAYS,
        "member_count": len(results),
        "members": results,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nDone. Written to {OUTPUT_FILE}")
    print(f"Top 5:")
    for r in results[:5]:
        print(f"  {r['rank']}. {r['name']} ({r['tier'].upper()}) — {r['probability']}%  [{r['mention_count']} mentions]")


if __name__ == "__main__":
    main()
