"""
test_chinese_rss2.py
--------------------
Follow-up test — fixes UTF-8 encoding issue and probes more feeds.
Focuses on the two feeds that returned 200 plus new candidates.
"""

import requests
import re
import warnings
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

TEST_NAMES_ZH = ["李强", "王毅", "赵乐际", "蔡奇", "丁薛祥", "李希", "王小洪", "陈文清"]

HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/122.0.0.0 Safari/537.36",
    "Accept":          "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept-Charset":  "utf-8",
}

FEEDS = [
    # Known working — re-test with forced UTF-8
    {"name": "Xinhua Politics (utf-8 forced)",     "url": "http://www.xinhuanet.com/politics/news_politics.xml"},
    {"name": "People's Daily politics (utf-8)",    "url": "http://www.people.com.cn/rss/politics.xml"},

    # New Xinhua candidates
    {"name": "Xinhua politics2",  "url": "http://www.xinhuanet.com/politics/index.htm"},
    {"name": "Xinhua leaders2",   "url": "http://www.xinhuanet.com/politics/ywxx.xml"},
    {"name": "Xinhua leaders3",   "url": "http://www.xinhuanet.com/politics/leaders.xml"},
    {"name": "Xinhua MRDX",       "url": "http://www.xinhuanet.com/politics/mrdx.xml"},
    {"name": "Xinhua zhengzhi",   "url": "http://www.xinhuanet.com/zhengzhi/index.htm"},
    {"name": "Xinhua renwen",     "url": "http://www.xinhuanet.com/politics/renwu.xml"},
    {"name": "Xinhua huiyi",      "url": "http://www.xinhuanet.com/politics/huiyi.xml"},
    {"name": "Xinhua yaowen",     "url": "http://www.xinhuanet.com/politics/yaowen.xml"},

    # People's Daily new candidates
    {"name": "People's Daily yaowen",    "url": "http://politics.people.com.cn/rss/yaowen.xml"},
    {"name": "People's Daily leaders2",  "url": "http://politics.people.com.cn/rss/leaders.xml"},
    {"name": "People's Daily shizheng",  "url": "http://politics.people.com.cn/rss/shizheng.xml"},
    {"name": "People's Daily zhengzhi",  "url": "http://www.people.com.cn/rss/zhengzhi.xml"},
    {"name": "People's Daily renmin",    "url": "http://paper.people.com.cn/rmrb/rss/rmrb.xml"},

    # Xinhuanet new domain variant
    {"name": "news.cn politics",  "url": "http://www.news.cn/politics/rss.xml"},
    {"name": "news.cn leaders",   "url": "http://www.news.cn/politics/leaders/rss.xml"},
    {"name": "news.cn china",     "url": "http://www.news.cn/china/rss.xml"},
]


def probe(feed: dict):
    print(f"\n{'─' * 60}")
    print(f"FEED: {feed['name']}")

    try:
        r = requests.get(feed["url"], headers=HEADERS, timeout=10, allow_redirects=True)
        print(f"STATUS: {r.status_code} | BYTES: {len(r.content)}")

        if r.status_code != 200:
            print(f"SKIP")
            return

        # Force UTF-8 regardless of what server claims
        r.encoding = "utf-8"
        content    = r.text

        soup     = BeautifulSoup(content, "html.parser")
        text_len = len(soup.get_text(strip=True))

        if text_len < 100:
            print(f"⚠ Empty/JS-rendered ({text_len} chars)")
            return

        items = soup.find_all("item") or soup.find_all("entry")
        print(f"ITEMS: {len(items)} | TEXT: {text_len} chars")

        if not items:
            print(f"No RSS items found")
            print(f"PREVIEW: {content[:300]}")
            return

        # Sample first 3 items
        print("SAMPLE ITEMS:")
        for item in items[:3]:
            title = item.find("title")
            date  = (item.find("pubdate") or item.find("published")
                     or item.find("updated") or item.find("dc:date"))
            t = title.get_text(strip=True) if title else "?"
            d = date.get_text(strip=True)[:20] if date else "?"
            # Strip CDATA wrapper if present
            t = re.sub(r'<!\[CDATA\[|\]\]>', '', t).strip()
            print(f"  [{d}] {t[:70]}")

        # Name hit count
        print("NAME HITS:")
        name_hits = {}
        for name in TEST_NAMES_ZH:
            count = content.count(name)
            name_hits[name] = count
            if count > 0:
                print(f"  ✓ {name}: {count}")

        total_hits = sum(name_hits.values())
        if total_hits == 0:
            print(f"  (none of the target names found)")

        # Check freshness — find most recent date
        all_dates = []
        for item in items:
            date = (item.find("pubdate") or item.find("published")
                    or item.find("updated") or item.find("dc:date"))
            if date:
                all_dates.append(date.get_text(strip=True)[:20])
        if all_dates:
            print(f"MOST RECENT DATE: {sorted(all_dates)[-1]}")
            print(f"OLDEST DATE: {sorted(all_dates)[0]}")

    except requests.exceptions.Timeout:
        print("TIMEOUT")
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")


if __name__ == "__main__":
    print(f"Testing {len(FEEDS)} feeds with forced UTF-8")
    print(f"Targets: {TEST_NAMES_ZH}\n")
    for feed in FEEDS:
        probe(feed)
    print(f"\n{'─' * 60}")
    print("Done.")
