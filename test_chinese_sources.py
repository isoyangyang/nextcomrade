"""
test_chinese_sources.py
-----------------------
Tests whether Xinhua and People's Daily Chinese search pages
are scrapeable from GitHub Actions.

Searches for 李强 (Li Qiang) on both sites and reports:
  - HTTP status code
  - Whether result count is in the HTML
  - Whether article titles are in the HTML
  - Whether content is JS-rendered or static
  - Raw HTML preview so we can identify the right CSS selectors

Run: python test_chinese_sources.py
"""

import requests
import re
from bs4 import BeautifulSoup
from urllib.parse import quote

TEST_NAME_ZH  = "李强"    # Li Qiang — well-known, should return many results
TEST_NAME_ZH2 = "王毅"    # Wang Yi — high volume, good second test

HEADERS_ZH = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36",
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
    "Connection":      "keep-alive",
    "Referer":         "https://www.xinhuanet.com/",
}

SOURCES = [
    {
        "name":    "Xinhua Chinese search (so.news.cn)",
        "url":     f"https://so.news.cn/getNews?keyword={quote(TEST_NAME_ZH)}&lang=cn&curPage=1&sortField=0",
        "type":    "json",
    },
    {
        "name":    "Xinhua Chinese search page",
        "url":     f"https://so.news.cn/#search?keyword={quote(TEST_NAME_ZH)}&lang=cn",
        "type":    "html",
    },
    {
        "name":    "Xinhua alternate JSON endpoint",
        "url":     f"https://so.news.cn/getNews?keyword={quote(TEST_NAME_ZH)}&lang=cn&curPage=1&pageSize=10",
        "type":    "json",
    },
    {
        "name":    "People's Daily search",
        "url":     f"http://search.people.com.cn/search?keyword={quote(TEST_NAME_ZH)}&channel=1",
        "type":    "html",
    },
    {
        "name":    "People's Daily search (SSL)",
        "url":     f"https://search.people.com.cn/search?keyword={quote(TEST_NAME_ZH)}&channel=1",
        "type":    "html",
    },
    {
        "name":    "People's Daily complex search",
        "url":     f"http://search.people.com.cn/complexSearch/search.do?keyword={quote(TEST_NAME_ZH)}&src=search",
        "type":    "html",
    },
    {
        "name":    "Xinhua full-text search API",
        "url":     f"https://search.news.cn/search?keyword={quote(TEST_NAME_ZH)}&lang=cn",
        "type":    "html",
    },
    {
        "name":    "Xinhua new search endpoint",
        "url":     f"https://so.news.cn/getNews",
        "params":  {"keyword": TEST_NAME_ZH, "lang": "cn", "curPage": 1, "pageSize": 10},
        "type":    "json_params",
    },
]


def probe(source: dict):
    print(f"\n{'─' * 65}")
    print(f"SOURCE: {source['name']}")
    url = source["url"]
    print(f"URL:    {url[:80]}")

    try:
        params = source.get("params")
        if params:
            r = requests.get(url, params=params, headers=HEADERS_ZH, timeout=12)
        else:
            r = requests.get(url, headers=HEADERS_ZH, timeout=12, allow_redirects=True)

        print(f"STATUS: {r.status_code}")
        print(f"FINAL URL: {r.url[:80]}")
        print(f"CONTENT-TYPE: {r.headers.get('Content-Type','?')[:60]}")
        print(f"BYTES: {len(r.content)}")
        print(f"ENCODING: {r.encoding}")

        content = r.text

        # ── Try JSON ──────────────────────────────────────────────────────────
        if source["type"] in ("json", "json_params") or "json" in r.headers.get("Content-Type",""):
            try:
                data = r.json()
                print(f"RESPONSE: JSON")
                print(f"TOP KEYS: {list(data.keys())[:10]}")

                # Hunt for total count
                def find_total(obj, depth=0):
                    if depth > 5: return
                    if isinstance(obj, dict):
                        for k, v in obj.items():
                            if k.lower() in ("total","count","totalhits","totalresults","num"):
                                print(f"  COUNT FIELD [{k}]: {v}")
                            find_total(v, depth+1)
                    elif isinstance(obj, list) and obj:
                        find_total(obj[0], depth+1)
                find_total(data)

                # Hunt for article list
                def find_articles(obj, depth=0):
                    if depth > 5: return
                    if isinstance(obj, dict):
                        for k, v in obj.items():
                            if isinstance(v, list) and v and isinstance(v[0], dict):
                                print(f"  ARTICLE LIST [{k}]: {len(v)} items")
                                first = v[0]
                                print(f"    FIRST ITEM KEYS: {list(first.keys())[:10]}")
                                title_key = next((k2 for k2 in first if 'title' in k2.lower() or '标题' in k2), None)
                                if title_key:
                                    print(f"    TITLE SAMPLE: {first[title_key][:80]}")
                            find_articles(v, depth+1)
                find_articles(data)

                print(f"RAW PREVIEW: {str(data)[:400]}")
                return

            except Exception:
                pass

        # ── HTML parsing ──────────────────────────────────────────────────────
        print(f"RESPONSE: HTML")

        # Check for JS rendering (empty body)
        text_len = len(BeautifulSoup(content, "html.parser").get_text(strip=True))
        print(f"TEXT LENGTH: {text_len} chars")

        if text_len < 200:
            print("⚠ Very little text — likely JS-rendered")
            print(f"RAW HTML:\n{content[:600]}")
            return

        soup = BeautifulSoup(content, "html.parser")

        # Look for result count patterns
        count_patterns = [
            r'找到[约]?\s*([0-9,，]+)\s*条',
            r'共\s*([0-9,，]+)\s*条',
            r'约\s*([0-9,，]+)\s*个',
            r'([0-9,，]+)\s*条结果',
            r'结果[：:]\s*([0-9,，]+)',
            r'共找到.*?([0-9,，]+)',
        ]
        for pattern in count_patterns:
            m = re.search(pattern, content)
            if m:
                print(f"✓ RESULT COUNT FOUND: {m.group(0)} → {m.group(1)}")
                break
        else:
            # Try to find any number near result-related words
            numbers = re.findall(r'[0-9]{2,6}', content[:3000])
            print(f"NUMBERS IN FIRST 3K CHARS: {numbers[:15]}")

        # Check for article titles containing Chinese name
        title_tags = soup.find_all(["h2","h3","h4","a"], string=lambda t: t and TEST_NAME_ZH in t)
        print(f"ELEMENTS CONTAINING '{TEST_NAME_ZH}': {len(title_tags)}")
        if title_tags:
            print(f"  SAMPLE: {title_tags[0].get_text(strip=True)[:100]}")

        # Find all element classes that might be result containers
        all_classes = set()
        for el in soup.find_all(["div","ul","li","article"], limit=100):
            if el.get("class"):
                all_classes.add(" ".join(el["class"]))
        if all_classes:
            print(f"ELEMENT CLASSES: {list(all_classes)[:20]}")

        # Print raw preview
        print(f"HTML PREVIEW (first 800 chars):\n{content[:800]}")

    except requests.exceptions.Timeout:
        print("STATUS: TIMEOUT")
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")


if __name__ == "__main__":
    print(f"Testing Chinese-language sources for: {TEST_NAME_ZH} (Li Qiang)")
    for source in SOURCES:
        probe(source)
    print(f"\n{'─' * 65}")
    print("Done. Share the full output.")
