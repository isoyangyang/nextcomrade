"""
scrape_scores.py
----------------
Fetches NewsAPI mention counts for each CCP member over the last 30 days,
computes a recency-weighted score, and writes scores.json for the frontend.

Scoring pipeline per member:
  1. Base score       — recency-weighted NewsAPI mention count across 4 weekly buckets
  2. Xi boost         — articles co-mentioning Xi Jinping count 3x (base + 2x extra)
  3. Age penalty      — members over 68 have score multiplied by AGE_PENALTY_FACTOR
  4. Fixed scores     — certain members (Wang Huning) hardcoded regardless of media
  5. Tier floor       — minimum score per tier so hierarchy is preserved
  6. Position bonus   — top 30 members only: fetch recent articles and check whether
                        the member appears in the headline or lead paragraph.
                        Headline mention  → POSITION_HEADLINE_MULT  (default 1.6x)
                        Lead para mention → POSITION_LEAD_MULT      (default 1.25x)
                        Deeper mention    → no bonus (1.0x)

Data source: NewsAPI.org (free tier, requires NEWSAPI_KEY env variable)
  Filters to Xinhua English source to keep data provenance consistent.

Run locally:  NEWSAPI_KEY=your_key python scrape_scores.py
Run via CI:   see .github/workflows/update_scores.yml

Dependencies: pip install requests beautifulsoup4
"""

import json
import os
import time
import datetime
import requests
from bs4 import BeautifulSoup

# ── CONFIG ────────────────────────────────────────────────────────────────────

LOOKBACK_DAYS           = 7     # 1 weekly bucket — keeps requests within NewsAPI free tier (100/day)
RECENCY_HALFLIFE        = 7     # days — most recent week counts 2x vs week prior
REQUEST_DELAY           = 1.0   # seconds between requests (NewsAPI is more tolerant)
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
POSITION_REQUEST_DELAY  = 2.0   # delay for article page fetches

# NewsAPI config
NEWSAPI_KEY     = os.environ.get("NEWSAPI_KEY", "")
NEWSAPI_URL     = "https://newsapi.org/v2/everything"
NEWSAPI_SOURCES = "xinhua"      # filter to Xinhua English

# ── MEMBERS ───────────────────────────────────────────────────────────────────

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

# ── FIXED SCORES ──────────────────────────────────────────────────────────────

FIXED_SCORES = {
    "Wang Huning": 0.08,
}

# ── TIER FLOORS ───────────────────────────────────────────────────────────────

TIER_FLOOR = {
    "psc": 5.0,
    "pb":  1.0,
    "cc":  0.1,
}

# ── NEWSAPI SEARCH ────────────────────────────────────────────────────────────

def _newsapi_get(query: str, date_from: str, date_to: str) -> int:
    """
    Search NewsAPI for articles matching query within a date window.
    Returns total result count.
    """
    if not NEWSAPI_KEY:
        raise RuntimeError("NEWSAPI_KEY environment variable not set")

    params = {
        "q":        query,
        "sources":  NEWSAPI_SOURCES,
        "from":     date_from,
        "to":       date_to,
        "language": "en",
        "pageSize": 1,          # we only need the count, not the articles
        "apiKey":   NEWSAPI_KEY,
    }
    try:
        r = requests.get(NEWSAPI_URL, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "ok":
            print(f"    WARNING: NewsAPI error — {data.get('message', 'unknown')}")
            return 0
        return int(data.get("totalResults", 0))
    except Exception as e:
        print(f"    WARNING: NewsAPI fetch failed [{query[:50]}]: {e}")
        return 0


def _newsapi_get_articles(query: str, date_from: str, n: int) -> list:
    """
    Return up to n recent articles (title + url + description) for the position pass.
    """
    if not NEWSAPI_KEY:
        return []

    params = {
        "q":        query,
        "sources":  NEWSAPI_SOURCES,
        "from":     date_from,
        "language": "en",
        "sortBy":   "publishedAt",
        "pageSize": n,
        "apiKey":   NEWSAPI_KEY,
    }
    try:
        r = requests.get(NEWSAPI_URL, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "ok":
            return []
        return data.get("articles", [])
    except Exception as e:
        print(f"    WARNING: NewsAPI article fetch failed [{query[:50]}]: {e}")
        return []


def fetch_mention_count(name: str, date_from: str, date_to: str) -> int:
    return _newsapi_get(f'"{name}"', date_from, date_to)


def fetch_xi_cooccurrence_count(name: str, date_from: str, date_to: str) -> int:
    return _newsapi_get(f'"{XI_NAME}" "{name}"', date_from, date_to)


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
    Splits into weekly buckets with exponential recency decay.
    Returns (total_score, raw_mention_total, xi_cooccurrence_total, last_seen_date_str).
    """
    buckets = []
    weeks   = LOOKBACK_DAYS // 7
    today   = datetime.date.today()

    for w in range(weeks):
        date_to   = (today - datetime.timedelta(days=w * 7)).strftime("%Y-%m-%d")
        date_from = (today - datetime.timedelta(days=(w + 1) * 7)).strftime("%Y-%m-%d")

        base_count = 0
        for name in name_variants:
            base_count += fetch_mention_count(name, date_from, date_to)
            time.sleep(REQUEST_DELAY)

        xi_count = 0
        for name in name_variants:
            xi_count += fetch_xi_cooccurrence_count(name, date_from, date_to)
            time.sleep(REQUEST_DELAY)

        boosted_count  = base_count + (xi_count * XI_BOOST_MULTIPLIER)
        recency_weight = 2 ** (w * -7 / RECENCY_HALFLIFE)
        buckets.append((base_count, xi_count, boosted_count, recency_weight))

    raw_total = sum(b  for b,  _x, _bc, _rw in buckets)
    xi_total  = sum(x  for _b,  x, _bc, _rw in buckets)
    score     = sum(bc * rw for _b, _x,  bc,  rw in buckets)

    last_seen = "Unknown"
    for w, (base_count, _x, _bc, _rw) in enumerate(buckets):
        if base_count > 0:
            last_seen = (today - datetime.timedelta(days=w * 7)).strftime("%Y-%m-%d")
            break

    return score, raw_total, xi_total, last_seen


# ── ARTICLE POSITION SCORING ──────────────────────────────────────────────────

def score_article_position(article: dict, name: str) -> str:
    """
    Given a NewsAPI article dict, determine where `name` appears.
    NewsAPI returns title, description (lead snippet), and url.
    We check title first, then description, then fetch the full page as fallback.

    Returns: 'headline', 'lead', 'body', or 'none'
    """
    name_lower = name.lower()

    # Check title (headline)
    title = article.get("title") or ""
    if name_lower in title.lower():
        return "headline"

    # Check description (lead paragraph snippet — usually first 1-2 sentences)
    description = article.get("description") or ""
    if name_lower in description.lower():
        return "lead"

    # Fallback: fetch full article and check body
    url = article.get("url", "")
    if url:
        try:
            headers = {"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"}
            r = requests.get(url, headers=headers, timeout=12)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            paragraphs = soup.find_all("p")
            body_text = " ".join(p.get_text(" ", strip=True) for p in paragraphs)
            if name_lower in body_text.lower():
                return "body"
        except Exception as e:
            print(f"      WARNING: article page fetch failed: {e}")

    return "none"


def position_multiplier(name_variants: tuple) -> tuple:
    """
    Fetch recent articles and determine best position for this member.
    Returns (multiplier, human_readable_label).
    """
    primary_name = name_variants[0]
    today        = datetime.date.today()
    date_from    = (today - datetime.timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    articles = _newsapi_get_articles(f'"{primary_name}"', date_from, POSITION_ARTICLES)
    time.sleep(REQUEST_DELAY)

    if not articles:
        return 1.0, "no articles"

    headline_count = 0
    lead_count     = 0
    best_position  = "body"

    for article in articles[:POSITION_ARTICLES]:
        pos = score_article_position(article, primary_name)
        if pos == "headline":
            headline_count += 1
            best_position = "headline"
        elif pos == "lead" and best_position != "headline":
            lead_count += 1
            best_position = "lead"
        time.sleep(POSITION_REQUEST_DELAY)

    checked = len(articles[:POSITION_ARTICLES])
    if best_position == "headline":
        return POSITION_HEADLINE_MULT, f"headline ({headline_count}/{checked} articles)"
    elif best_position == "lead":
        return POSITION_LEAD_MULT, f"lead para ({lead_count}/{checked} articles)"
    else:
        return 1.0, f"body only ({checked} articles)"


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    if not NEWSAPI_KEY:
        print("ERROR: NEWSAPI_KEY environment variable is not set.")
        print("  Local: NEWSAPI_KEY=your_key python scrape_scores.py")
        print("  CI:    add NEWSAPI_KEY to GitHub Actions secrets")
        return

    print(f"Starting scrape — {datetime.datetime.now().isoformat()}")
    print(f"Source: NewsAPI (xinhua) | Lookback: {LOOKBACK_DAYS}d | "
          f"Xi boost: {XI_BOOST_MULTIPLIER}x | Age penalty >{RETIREMENT_AGE}: "
          f"{AGE_PENALTY_FACTOR}x | Position pass: top {POSITION_TOP_N} | "
          f"Members: {len(MEMBERS)}\n")

    results   = []
    penalised = []

    # ── Phase 1: base scoring ──────────────────────────────────────────────
    print("── Phase 1: Base scoring ──────────────────────────────────────")
    for name_variants, tier, role, birth_year in MEMBERS:
        primary_name = name_variants[0]
        age_str      = str(birth_year) if birth_year else "?"
        print(f"  [{tier.upper()}] {primary_name} (b.{age_str})...")

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

        score, count, xi_count, last_seen = weighted_score(name_variants)
        print(f"    -> mentions: {count} (xi co-occur: {xi_count}), "
              f"weighted: {score:.2f}, last seen: {last_seen}")

        penalty       = age_penalty_multiplier(birth_year)
        age_penalised = penalty < 1.0
        if age_penalised:
            age = datetime.date.today().year - birth_year
            print(f"    -> age penalty (age {age}): {score:.2f} -> {score * penalty:.2f}")
            penalised.append(primary_name)
        score *= penalty

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

    # intermediate sort to find top N
    results.sort(key=lambda r: r["raw_score"], reverse=True)

    # ── Phase 2: position pass — top N only ───────────────────────────────
    print(f"\n── Phase 2: Position pass (top {POSITION_TOP_N}) ─────────────────────")
    for r in results[:POSITION_TOP_N]:
        if r.get("fixed"):
            r["position_label"]      = "fixed"
            r["position_multiplier"] = 1.0
            continue

        print(f"  {r['name']}...")
        name_variants = next(m[0] for m in MEMBERS if m[0][0] == r["name"])
        mult, label   = position_multiplier(name_variants)
        r["position_multiplier"] = mult
        r["position_label"]      = label
        r["raw_score"]           = r["raw_score"] * mult
        print(f"    -> position: {label} → {mult}x → adjusted score {r['raw_score']:.2f}")

    for r in results[POSITION_TOP_N:]:
        if r["position_label"] == "pending":
            r["position_label"] = "not assessed"

    # ── Normalise, sort, rank ──────────────────────────────────────────────
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
