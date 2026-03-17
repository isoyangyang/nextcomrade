"""
test_chinese_rss.py
-------------------
Tests Chinese-language RSS feeds from Xinhua and People's Daily.
Checks accessibility, structure, and whether member names appear in content.

Run: python test_chinese_rss.py
"""

import requests
import re
from bs4 import BeautifulSoup

TEST_NAMES_ZH = ["李强", "王毅", "赵乐际", "蔡奇", "丁薛祥"]

HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/122.0.0.0 Safari/537.36",
    "Accept":          "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer":         "https://www.xinhuanet.com/",
}

FEEDS = [
    # Xinhua Chinese
    {"name": "Xinhua Politics",        "url": "http://www.xinhuanet.com/politics/news_politics.xml"},
    {"name": "Xinhua Leaders",         "url": "http://www.xinhuanet.com/politics/leaders/node_1.htm"},
    {"name": "Xinhua China",           "url": "http://www.xinhuanet.com/china/node_7852492.htm"},
    {"name": "Xinhua RSS politics",    "url": "https://www.xinhuanet.com/rss/politics.xml"},
    {"name": "Xinhua RSS china",       "url": "https://www.xinhuanet.com/rss/china.xml"},
    {"name": "Xinhua RSS leaders",     "url": "http://www.xinhuanet.com/rss/leaders.xml"},
    {"name": "Xinhua world",           "url": "http://www.xinhuanet.com/world/node_7852503.htm"},
    {"name": "Xinhua home XML",        "url": "https://www.xinhuanet.com/xhwz/newscenter/rss/mrdx.xml"},
    # People's Daily Chinese
    {"name": "People's Daily politics","url": "http://www.people.com.cn/rss/politics.xml"},
    {"name": "People's Daily leaders", "url": "http://www.people.com.cn/rss/leaders.xml"},
    {"name": "People's Daily china",   "url": "http://www.people.com.cn/rss/china.xml"},
    {"name": "People's Daily front",   "url": "http://www.people.com.cn/rss/rmrb.xml"},
    {"name": "People's Daily opinion", "url": "http://opinion.people.com.cn/rss.xml"},
    {"name": "People's Daily GB",      "url": "http://paper.people.com.cn/rmrb/rss.xml"},
    # CCTV
    {"name": "CCTV news",              "url": "http://www.cctv.com/lm/800/01/index.shtml"},
    {"name": "CCTV RSS",               "url": "https://news.cctv.com/rss/china.xml"},
]


def probe(feed: dict):
    print(f"\n{'─' * 60}")
    print(f"FEED: {feed['name']}")
    print(f"URL:  {feed['url']}")

    try:
        r = requests.get(feed["url"], headers=HEADERS, timeout=10, allow_redirects=True)
        print(f"STATUS: {r.status_code} | BYTES: {len(r.content)} | "
              f"TYPE: {r.headers.get('Content-Type','?')[:50]}")

        if r.status_code != 200:
            print(f"SKIPPING — non-200")
            return

        content = r.text

        # Try JSON
        try:
            data = r.json()
            print(f"FORMAT: JSON — keys: {list(data.keys())[:8]}")
            return
        except Exception:
            pass

        # Parse as XML/HTML
        soup = BeautifulSoup(content, "html.parser")
        text_len = len(soup.get_text(strip=True))
        print(f"FORMAT: XML/HTML | TEXT: {text_len} chars")

        if text_len < 100:
            print(f"⚠ Nearly empty — JS-rendered or blocked")
            print(f"RAW: {content[:300]}")
            return

        # Count RSS items
        items = soup.find_all("item")
        entries = soup.find_all("entry")   # Atom format
        print(f"RSS ITEMS: {len(items)} | ATOM ENTRIES: {len(entries)}")

        all_items = items or entries
        if not all_items:
            # Try other structures
            links = soup.find_all("a", href=True)
            zh_links = [a for a in links if any(n in a.get_text() for n in TEST_NAMES_ZH)]
            print(f"TOTAL LINKS: {len(links)} | LINKS WITH NAMES: {len(zh_links)}")
            if zh_links:
                print(f"  SAMPLE: {zh_links[0].get_text(strip=True)[:80]}")
            print(f"HTML PREVIEW: {content[:400]}")
            return

        # Analyse items
        print(f"\nSAMPLE ITEMS (first 3):")
        for item in all_items[:3]:
            title = item.find("title")
            link  = item.find("link")
            date  = item.find("pubdate") or item.find("published") or item.find("updated")
            desc  = item.find("description") or item.find("summary")
            t = title.get_text(strip=True) if title else "?"
            l = link.get_text(strip=True) if link else (link.get("href","") if link else "?")
            d = date.get_text(strip=True)[:20] if date else "?"
            print(f"  TITLE: {t[:70]}")
            print(f"  LINK:  {l[:70]}")
            print(f"  DATE:  {d}")
            print()

        # Check name presence
        full_text = content
        print("NAME HITS:")
        for name in TEST_NAMES_ZH:
            count = full_text.count(name)
            print(f"  {name}: {count} mentions")

        # Check if titles are in Chinese
        if all_items:
            first_title = all_items[0].find("title")
            if first_title:
                t = first_title.get_text(strip=True)
                has_chinese = bool(re.search(r'[\u4e00-\u9fff]', t))
                print(f"CHINESE CONTENT: {has_chinese} (sample: {t[:50]})")

    except requests.exceptions.Timeout:
        print("TIMEOUT")
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")


if __name__ == "__main__":
    print(f"Testing {len(FEEDS)} Chinese RSS feeds")
    print(f"Name targets: {TEST_NAMES_ZH}\n")
    for feed in FEEDS:
        probe(feed)
    print(f"\n{'─' * 60}")
    print("Done.")
