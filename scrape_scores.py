"""
scrape_scores.py
----------------
Fetches Xinhua mention counts for each CCP member over the last 30 days,
computes a recency-weighted score, and writes scores.json for the frontend.

Scoring pipeline per member:
  1. Base score       — recency-weighted Xinhua mention count across 4 weekly buckets
  2. Xi boost         — articles co-mentioning Xi Jinping count 3x (base + 2x extra)
  3. Age penalty      — members over 68 have score multiplied by AGE_PENALTY_FACTOR
  4. Fixed scores     — certain members (Wang Huning) hardcoded regardless of media
  5. Tier floor       — minimum score per tier so hierarchy is preserved
  6. Position bonus   — top 30 members only: fetch recent articles and check whether
                        the member appears in the headline or lead paragraph.
                        Headline mention  → POSITION_HEADLINE_MULT  (default 1.6x)
                        Lead para mention → POSITION_LEAD_MULT      (default 1.25x)
                        Deeper mention    → no bonus (1.0x)
                        Final score = base_score × position_multiplier

Run locally:  python scrape_scores.py
Run via CI:   see .github/workflows/update_scores.yml

Dependencies: pip install requests beautifulsoup4
"""

import json
import time
import datetime
import requests
from bs4 import BeautifulSoup

# ── CONFIG ────────────────────────────────────────────────────────────────────

LOOKBACK_DAYS           = 30    # how far back to search
RECENCY_HALFLIFE        = 7     # days — most recent week counts 2x vs week prior
REQUEST_DELAY           = 2.0   # seconds between requests — be polite to Xinhua
OUTPUT_FILE             = "scores.json"

XI_NAME                 = "Xi Jinping"
XI_BOOST_MULTIPLIER     = 2     # co-occurrence articles added on top of base
                                # net effect: those articles count (1 + 2) = 3x

RETIREMENT_AGE          = 68    # informal CCP norm — anyone older penalised
AGE_PENALTY_FACTOR      = 0.15  # over-68 score × this (85% reduction)

POSITION_TOP_N          = 30    # only run article-position pass on top N members
POSITION_ARTICLES       = 5     # how many recent articles to fetch per member
POSITION_HEADLINE_MULT  = 1.6   # member named in headline → score × this
POSITION_LEAD_MULT      = 1.25  # member named in first 2 paragraphs → score × this
POSITION_REQUEST_DELAY  = 3.0   # slightly longer delay for article page fetches

# ── MEMBERS ───────────────────────────────────────────────────────────────────
# Each entry: (name_variants_tuple, tier, display_role, birth_year)
# Name variants are summed — handles romanisation differences.
# birth_year: use None if unknown (no age penalty applied).

MEMBERS = [
    # PSC
    (("Li Qiang",),                "psc", "Premier, State Council",             1959),
    (("Zhao Leji",),               "psc", "Chairman, NPC",                      1957),
    (("Wang Huning",),             "psc", "Chairman, CPPCC",                    1955),
    (("Cai Qi",),                  "psc", "Director, General Office",           1955),
    (("Ding Xuexiang",),           "psc", "Executive Vice Premier",             1962),
    (("Li Xi",),                   "psc", "Secretary, CCDI",                    1956),
    (("Han Zheng",),               "psc", "Vice President (State)",             1954),
    # Politburo
    (("Chen Wenqing",),            "pb",  "Director, Political-Legal Comm.",    1960),
    (("Zhang Youxia", "Zhang You-xia"), "pb", "Vice Chairman, CMC",            1950),
    (("He Weidong",),              "pb",  "Vice Chairman, CMC",                 1957),
    (("Liu Guozhong",),            "pb",  "Vice Premier",                       1960),
    (("Ma Xingrui",),              "pb",  "Party Secretary, Xinjiang",          1959),
    (("Yuan Jiajun",),             "pb",  "Party Secretary, Chongqing",         1961),
    (("Li Ganjie",),               "pb",  "Party Secretary, Guangdong",         1964),
    (("Shen Yiqin",),              "pb",  "Executive Vice Premier",             1966),
    (("Zhang Guoqing",),           "pb",  "Vice Premier",                       1964),
    (("Wang Yi",),                 "pb",  "Director, Foreign Affairs Comm.",    1953),
    (("Li Hongzhong",),            "pb",  "Deputy Chair, NPC",                  1956),
    (("Chen Min'er", "Chen Miner"),"pb",  "Party Secretary, Tianjin",           1960),
    (("Yin Li",),                  "pb",  "Party Secretary, Shanghai",          1962),
    (("He Lifeng",),               "pb",  "Vice Premier",                       1955),
    (("Liu Jinguo",),              "pb",  "Deputy Secretary, CCDI",             1961),
    (("Guo Shengkun",),            "pb",  "Deputy Chair, NPC",                  1956),
    (("Huang Kunming",),           "pb",  "Former Party Secretary, Guangdong",  1956),
    (("Dong Jun",),                "pb",  "Minister of National Defense",       1961),
    # Central Committee (selected)
    (("Chen Quanguo",),            "cc",  "Central Committee Member",           1955),
    (("Hu Chunhua",),              "cc",  "Central Committee Member",           1963),
    (("Li Shulei",),               "cc",  "Central Committee Member",           1965),
    (("Qin Gang",),                "cc",  "Central Committee Member",           1966),
    (("Chen Xi",),                 "cc",  "Central Committee Member",           1953),
    (("Bai Chunli",),              "cc",  "Central Committee Member",           1953),
    (("Cao Chunting",),            "cc",  "Central Committee Member",           1963),
    (("Chen Guangguo",),           "cc",  "Central Committee Member",           1959),
    (("Chen Jining",),             "cc",  "Central Committee Member",           1964),
    (("Fan Changlong",),           "cc",  "Central Committee Member",           1947),
    (("Fu Zhenghua",),             "cc",  "Central Committee Member",           1955),
    (("Gao Jin",),                 "cc",  "Central Committee Member",           1963),
    (("Han Changfu",),             "cc",  "Central Committee Member",           1960),
    (("Hu Heping",),               "cc",  "Central Committee Member",           1962),
    (("Jin Zhuanglong",),          "cc",  "Central Committee Member",           1964),
    (("Li Yunze",),                "cc",  "Central Committee Member",           1970),
    (("Liu He",),                  "cc",  "Central Committee Member",           1952),
    (("Liu Zhenli",),              "cc",  "Central Committee Member",           1964),
    (("Luo Huining",),             "cc",  "Central Committee Member",           1956),
    (("Ma Wenrui",),               "cc",  "Central Committee Member",           1959),
    (("Ni Yuefeng",),              "cc",  "Central Committee Member",           1965),
    (("Ning Jizhe",),              "cc",  "Central Committee Member",           1956),
    (("Pan Gongshen",),            "cc",  "Central Committee Member",           1963),
    (("Shi Taifeng",),             "cc",  "Central Committee Member",           1956),
    (("Sun Chunlan",),             "cc",  "Central Committee Member",           1950),
    (("Tang Yijun",),              "cc",  "Central Committee Member",           1964),
    (("Wang Xiaohong",),           "cc",  "Central Committee Member",           1963),
    (("Wei Fenghe",),              "cc",  "Central Committee Member",           1954),
    (("Xia Baolong",),             "cc",  "Central Committee Member",           1952),
    (("Xu Qiliang",),              "cc",  "Central Committee Member",           1954),
    (("Yang Xiaodu",),             "cc",  "Central Committee Member",           1963),
    (("Zhang Jun",),               "cc",  "Central Committee Member",           1963),
    (("Zhao Kezhi",),              "cc",  "Central Committee Member",           1953),
    (("Zhou Qiang",),              "cc",  "Central Committee Member",           1960),
]

# ── FIXED SCORES — override all scoring logic ─────────────────────────────────

FIXED_SCORES = {
    "Wang Huning": 0.08,   # ideologist, not a succession candidate
}

# ── TIER FLOOR WEIGHTS ────────────────────────────────────────────────────────
# Applied AFTER age penalty — over-68 PSC members still floor above CC.

TIER_FLOOR = {
    "psc": 5.0,
    "pb":  1.0,
    "cc":  0.1,
}

# ── XINHUA SEARCH ─────────────────────────────────────────────────────────────

XINHUA_SEARCH     = "https://so.news.cn/en/query"
XINHUA_ARTICLE    = "https://english.news.cn"   # base for resolving relative URLs

def _xinhua_get(keyword: str, days_ago_start: int, days_ago_end: int) -> int:
    """Shared Xinhua search — returns total result count for a keyword query."""
    today     = datetime.date.today()
    date_from = (today - datetime.timedelta(days=days_ago_start)).strftime("%Y-%m-%d")
    date_to   = (today - datetime.timedelta(days=days_ago_end)).strftime("%Y-%m-%d")

    params = {
        "keyword":   keyword,
        "lang":      "en",
        "sortField": "score",
        "dateFrom":  date_from,
        "dateTo":    date_to,
        "curPage":   1,
        "pageSize":  10,
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)",
        "Accept":     "application/json, text/plain, */*",
        "Referer":    "https://so.news.cn/",
    }
    try:
        r = requests.get(XINHUA_SEARCH, params=params, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        return int(
            data.get("data", {}).get("total", 0)
            or data.get("total", 0)
            or 0
        )
    except Exception as e:
        print(f"    WARNING: Xinhua search failed [{keyword[:50]}]: {e}")
        return 0


def _xinhua_get_urls(keyword: str, days_ago_start: int, n: int = POSITION_ARTICLES) -> list:
    """
    Return up to n article URLs from Xinhua search results for a keyword.
    Used for the article-position pass.
    """
    today     = datetime.date.today()
    date_from = (today - datetime.timedelta(days=days_ago_start)).strftime("%Y-%m-%d")
    date_to   = today.strftime("%Y-%m-%d")

    params = {
        "keyword":   keyword,
        "lang":      "en",
        "sortField": "date",      # most recent first
        "dateFrom":  date_from,
        "dateTo":    date_to,
        "curPage":   1,
        "pageSize":  n,
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)",
        "Accept":     "application/json, text/plain, */*",
        "Referer":    "https://so.news.cn/",
    }
    try:
        r = requests.get(XINHUA_SEARCH, params=params, headers=headers, timeout=10)
        r.raise_for_status()
        data  = r.json()
        items = (
            data.get("data", {}).get("list", [])
            or data.get("list", [])
            or []
        )
        urls = []
        for item in items:
            url = item.get("url") or item.get("link") or item.get("href") or ""
            if url:
                if url.startswith("/"):
                    url = XINHUA_ARTICLE + url
                urls.append(url)
        return urls
    except Exception as e:
        print(f"    WARNING: URL fetch failed [{keyword[:50]}]: {e}")
        return []


def fetch_mention_count(name: str, days_ago_start: int, days_ago_end: int) -> int:
    return _xinhua_get(f'"{name}"', days_ago_start, days_ago_end)


def fetch_xi_cooccurrence_count(name: str, days_ago_start: int, days_ago_end: int) -> int:
    return _xinhua_get(f'"{XI_NAME}" "{name}"', days_ago_start, days_ago_end)


# ── AGE PENALTY ───────────────────────────────────────────────────────────────

def age_penalty_multiplier(birth_year):
    if birth_year is None:
        return 1.0
    age = datetime.date.today().year - birth_year
    return AGE_PENALTY_FACTOR if age > RETIREMENT_AGE else 1.0


# ── WEIGHTED SCORE ────────────────────────────────────────────────────────────

def weighted_score(name_variants: tuple) -> tuple:
    """
    Recency-weighted, Xi-boosted mention score across LOOKBACK_DAYS.
    Returns (total_score, raw_mention_total, xi_cooccurrence_total, last_seen_date_str).
    """
    buckets = []
    weeks   = LOOKBACK_DAYS // 7

    for w in range(weeks):
        days_start = LOOKBACK_DAYS - (w * 7)
        days_end   = days_start - 7

        base_count = 0
        for name in name_variants:
            base_count += fetch_mention_count(name, days_start, days_end)
            time.sleep(REQUEST_DELAY)

        xi_count = 0
        for name in name_variants:
            xi_count += fetch_xi_cooccurrence_count(name, days_start, days_end)
            time.sleep(REQUEST_DELAY)

        boosted_count  = base_count + (xi_count * XI_BOOST_MULTIPLIER)
        recency_weight = 2 ** (w * -7 / RECENCY_HALFLIFE)
        buckets.append((base_count, xi_count, boosted_count, recency_weight))

    raw_total = sum(b  for b,  _x, _bc, _rw in buckets)
    xi_total  = sum(x  for _b,  x, _bc, _rw in buckets)
    score     = sum(bc * rw for _b, _x,  bc,  rw in buckets)

    last_seen = "Unknown"
    today = datetime.date.today()
    for w, (base_count, _x, _bc, _rw) in enumerate(buckets):
        if base_count > 0:
            last_seen = (today - datetime.timedelta(days=w * 7)).strftime("%Y-%m-%d")
            break

    return score, raw_total, xi_total, last_seen


# ── ARTICLE POSITION SCORING ──────────────────────────────────────────────────

def score_article_position(url: str, name: str) -> str:
    """
    Fetch a single Xinhua article and determine where `name` first appears:
      'headline'  — in the <title> or <h1>
      'lead'      — in the first 2 <p> tags of the article body
      'body'      — anywhere deeper
      'none'      — name not found (stale search result)

    Returns one of those four strings.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)",
        "Accept":     "text/html,application/xhtml+xml",
        "Referer":    "https://english.news.cn/",
    }
    try:
        r = requests.get(url, headers=headers, timeout=12)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Check headline — <title> and first <h1>
        title_text = soup.title.get_text(" ", strip=True) if soup.title else ""
        h1         = soup.find("h1")
        h1_text    = h1.get_text(" ", strip=True) if h1 else ""
        if name.lower() in title_text.lower() or name.lower() in h1_text.lower():
            return "headline"

        # Find article body paragraphs — Xinhua uses <div class="article"> or similar
        body = (
            soup.find("div", class_="article")
            or soup.find("div", id="article")
            or soup.find("article")
            or soup.find("div", class_="content")
            or soup.body
        )
        if not body:
            return "none"

        paragraphs = body.find_all("p")
        if not paragraphs:
            return "none"

        # Check lead (first 2 paragraphs)
        lead_text = " ".join(p.get_text(" ", strip=True) for p in paragraphs[:2])
        if name.lower() in lead_text.lower():
            return "lead"

        # Check rest of body
        body_text = " ".join(p.get_text(" ", strip=True) for p in paragraphs[2:])
        if name.lower() in body_text.lower():
            return "body"

        return "none"

    except Exception as e:
        print(f"      WARNING: article fetch failed [{url[:60]}]: {e}")
        return "none"


def position_multiplier(name_variants: tuple) -> tuple:
    """
    Fetch up to POSITION_ARTICLES recent articles for this member,
    score each for position, and return an aggregate multiplier.

    Logic:
      - Take the best position found across all sampled articles.
      - Headline in any article → POSITION_HEADLINE_MULT
      - Lead in any article (no headline) → POSITION_LEAD_MULT
      - Body only → 1.0 (no bonus)
      - No articles found → 1.0

    Also returns a human-readable position_label for the JSON output.
    """
    primary_name = name_variants[0]
    urls = _xinhua_get_urls(f'"{primary_name}"', LOOKBACK_DAYS)
    time.sleep(REQUEST_DELAY)

    if not urls:
        return 1.0, "no articles"

    best_position = "body"   # default — assume body-level if found at all
    headline_count = 0
    lead_count     = 0
    checked        = 0

    for url in urls[:POSITION_ARTICLES]:
        pos = score_article_position(url, primary_name)
        checked += 1
        if pos == "headline":
            headline_count += 1
            best_position = "headline"
        elif pos == "lead" and best_position != "headline":
            lead_count += 1
            best_position = "lead"
        time.sleep(POSITION_REQUEST_DELAY)

    if best_position == "headline":
        mult  = POSITION_HEADLINE_MULT
        label = f"headline ({headline_count}/{checked} articles)"
    elif best_position == "lead":
        mult  = POSITION_LEAD_MULT
        label = f"lead para ({lead_count}/{checked} articles)"
    else:
        mult  = 1.0
        label = f"body only ({checked} articles)"

    return mult, label


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print(f"Starting scrape — {datetime.datetime.now().isoformat()}")
    print(f"Lookback: {LOOKBACK_DAYS}d | Xi boost: {XI_BOOST_MULTIPLIER}x | "
          f"Age penalty >{RETIREMENT_AGE}: {AGE_PENALTY_FACTOR}x | "
          f"Position pass: top {POSITION_TOP_N} | Members: {len(MEMBERS)}\n")

    results   = []
    penalised = []

    # ── Phase 1: base scoring for all members ─────────────────────────────
    print("── Phase 1: Base scoring ──────────────────────────────────────")
    for name_variants, tier, role, birth_year in MEMBERS:
        primary_name = name_variants[0]
        age_str      = str(birth_year) if birth_year else "?"
        print(f"  [{tier.upper()}] {primary_name} (b.{age_str})...")

        # Fixed override
        if primary_name in FIXED_SCORES:
            age = datetime.date.today().year - birth_year if birth_year else None
            results.append({
                "name":                  primary_name,
                "tier":                  tier,
                "role":                  role,
                "birth_year":            birth_year,
                "age":                   age,
                "raw_score":             FIXED_SCORES[primary_name],
                "mention_count":         0,
                "xi_cooccurrence_count": 0,
                "last_seen":             "N/A",
                "age_penalised":         False,
                "position_label":        "fixed",
                "position_multiplier":   1.0,
                "fixed":                 True,
            })
            print(f"    -> fixed at {FIXED_SCORES[primary_name]}")
            continue

        # Fetch base + xi scores
        score, count, xi_count, last_seen = weighted_score(name_variants)
        print(f"    -> mentions: {count} (xi co-occur: {xi_count}), "
              f"weighted: {score:.2f}, last seen: {last_seen}")

        # Age penalty
        penalty       = age_penalty_multiplier(birth_year)
        age_penalised = penalty < 1.0
        if age_penalised:
            age = datetime.date.today().year - birth_year
            print(f"    -> age penalty (age {age}): {score:.2f} -> {score * penalty:.2f}")
            penalised.append(primary_name)
        score *= penalty

        # Tier floor
        final_score = max(score, TIER_FLOOR[tier])
        age_val     = datetime.date.today().year - birth_year if birth_year else None

        results.append({
            "name":                  primary_name,
            "tier":                  tier,
            "role":                  role,
            "birth_year":            birth_year,
            "age":                   age_val,
            "raw_score":             final_score,
            "mention_count":         count,
            "xi_cooccurrence_count": xi_count,
            "last_seen":             last_seen,
            "age_penalised":         age_penalised,
            "position_label":        "pending",
            "position_multiplier":   1.0,
            "fixed":                 False,
        })

    # ── Intermediate sort to identify top N for position pass ─────────────
    results.sort(key=lambda r: r["raw_score"], reverse=True)

    # ── Phase 2: article position pass — top N only ────────────────────────
    print(f"\n── Phase 2: Article position pass (top {POSITION_TOP_N}) ────────────────")
    for r in results[:POSITION_TOP_N]:
        if r.get("fixed"):
            r["position_label"]      = "fixed"
            r["position_multiplier"] = 1.0
            continue

        print(f"  {r['name']}...")
        name_variants = next(
            m[0] for m in MEMBERS if m[0][0] == r["name"]
        )
        mult, label = position_multiplier(name_variants)
        r["position_multiplier"] = mult
        r["position_label"]      = label
        r["raw_score"]           = r["raw_score"] * mult
        print(f"    -> position: {label} → multiplier {mult}x "
              f"→ adjusted score {r['raw_score']:.2f}")

    # Mark remaining members as not assessed
    for r in results[POSITION_TOP_N:]:
        if r["position_label"] == "pending":
            r["position_label"] = "not assessed"

    # ── Final normalise, sort, rank ────────────────────────────────────────
    total = sum(r["raw_score"] for r in results)
    for r in results:
        r["probability"] = round(r["raw_score"] / total * 100, 4)

    results.sort(key=lambda r: r["probability"], reverse=True)
    for i, r in enumerate(results):
        r["rank"] = i + 1

    # ── Write output ───────────────────────────────────────────────────────
    output = {
        "generated_at":  datetime.datetime.utcnow().isoformat() + "Z",
        "lookback_days": LOOKBACK_DAYS,
        "member_count":  len(results),
        "members":       results,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n{'─' * 60}")
    print(f"Done. Written to {OUTPUT_FILE}")
    if penalised:
        print(f"Age-penalised ({len(penalised)}): {', '.join(penalised)}")
    print(f"\nTop 10:")
    for r in results[:10]:
        xi   = r.get("xi_cooccurrence_count", 0)
        pos  = r.get("position_label", "—")
        flag = " [AGE]"   if r.get("age_penalised") else ""
        flag = " [FIXED]" if r.get("fixed")         else flag
        print(f"  {r['rank']:>3}. {r['name']:<22} ({r['tier'].upper()}) "
              f"{r['probability']:>6.2f}%  "
              f"[{r['mention_count']} mentions, {xi} w/Xi, pos: {pos}]{flag}")


if __name__ == "__main__":
    main()
