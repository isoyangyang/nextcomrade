"""
pipeline/fetch_stealth.py
-------------------------
Stealth HTTP session that mimics real browser behaviour.
Used by 01_fetch_media.py to avoid bot detection on Chinese news sites.

Techniques:
  1. Full realistic browser header set including sec-ch-ua, sec-fetch-* headers
  2. Session-based requests — loads homepage first to get cookies
  3. Random jitter on all request delays
  4. Rotating User-Agent strings across runs
  5. Realistic Referer chain
  6. Longer pauses every N requests to mimic human browsing rhythm
  7. Random member ordering across daily runs
"""

import time
import random
import datetime
import requests
from typing import Optional

# ── USER AGENT POOL ───────────────────────────────────────────────────────────
# Current realistic Chrome/Firefox strings — update periodically

USER_AGENTS = [
    # Chrome 122 Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    # Chrome 121 Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    # Chrome 122 macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    # Firefox 123 Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) "
    "Gecko/20100101 Firefox/123.0",
    # Firefox 122 macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:122.0) "
    "Gecko/20100101 Firefox/122.0",
    # Edge 122
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
]

# Pick one UA per run and stick with it — consistency within a session
# is more realistic than changing per request
_SESSION_UA = random.choice(USER_AGENTS)


def _chrome_headers(referer: Optional[str] = None, ua: Optional[str] = None) -> dict:
    """
    Return a realistic Chrome header set.
    Includes sec-ch-ua, sec-fetch-* and other headers bots typically omit.
    """
    _ua = ua or _SESSION_UA
    is_firefox = "Firefox" in _ua

    headers = {
        "User-Agent":      _ua,
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,"
                           "image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection":      "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control":   "max-age=0",
    }

    if referer:
        headers["Referer"] = referer

    # Chrome-specific security headers — Firefox doesn't send these
    if not is_firefox:
        # sec-ch-ua varies by Chrome version — extract from UA
        chrome_ver = "122"
        for part in _ua.split("Chrome/"):
            if len(part) > 3:
                chrome_ver = part[:3]
                break
        headers.update({
            "sec-ch-ua":          f'"Chromium";v="{chrome_ver}", '
                                   f'"Google Chrome";v="{chrome_ver}", '
                                   f'"Not:A-Brand";v="99"',
            "sec-ch-ua-mobile":   "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Sec-Fetch-Site":     "same-origin",
            "Sec-Fetch-Mode":     "navigate",
            "Sec-Fetch-User":     "?1",
            "Sec-Fetch-Dest":     "document",
        })

    return headers


def _jitter(base: float, variance: float = 0.5) -> float:
    """Return base ± variance seconds, always positive."""
    return max(0.5, base + random.uniform(-variance, variance))


class StealthSession:
    """
    A requests.Session wrapper that behaves like a human browser.
    Maintains cookies, rotates timing, and builds realistic referer chains.
    """

    def __init__(
        self,
        base_delay: float = 4.0,
        pause_every: int = 8,
        pause_duration: float = 20.0,
    ):
        self.session       = requests.Session()
        self.base_delay    = base_delay
        self.pause_every   = pause_every
        self.pause_duration = pause_duration
        self.request_count = 0
        self.last_url      = None
        self.ua            = _SESSION_UA

    def warm_up(self, homepage_url: str) -> bool:
        """
        Load the homepage to establish cookies and session state.
        Returns True if successful.
        """
        try:
            print(f"      [stealth] warming up session on {homepage_url[:50]}...")
            r = self.session.get(
                homepage_url,
                headers=_chrome_headers(ua=self.ua),
                timeout=12,
                allow_redirects=True,
            )
            self.last_url = r.url
            time.sleep(_jitter(2.0, 1.0))   # brief pause after landing on homepage
            print(f"      [stealth] session warm — cookies: {len(self.session.cookies)}")
            return r.status_code == 200
        except Exception as e:
            print(f"      [stealth] warm-up failed: {e}")
            return False

    def get(self, url: str, params: dict = None, timeout: int = 15) -> requests.Response:
        """
        Make a GET request with human-like timing and headers.
        Automatically pauses every N requests.
        """
        self.request_count += 1

        # Long pause every N requests — mimics human taking a break
        if self.request_count > 1 and self.request_count % self.pause_every == 0:
            pause = _jitter(self.pause_duration, 5.0)
            print(f"      [stealth] natural pause {pause:.0f}s after {self.request_count} requests...")
            time.sleep(pause)

        # Build headers with realistic referer
        headers = _chrome_headers(
            referer=self.last_url,
            ua=self.ua,
        )

        r = self.session.get(
            url,
            params=params,
            headers=headers,
            timeout=timeout,
            allow_redirects=True,
        )
        self.last_url = r.url

        # Random delay after request
        time.sleep(_jitter(self.base_delay, 1.5))

        return r

    def get_json(self, url: str, params: dict = None) -> dict:
        """GET request expecting JSON response."""
        headers = _chrome_headers(referer=self.last_url, ua=self.ua)
        headers["Accept"] = "application/json, text/plain, */*"
        # JSON API requests look like XHR, not document navigation
        headers["Sec-Fetch-Mode"] = "cors"
        headers["Sec-Fetch-Dest"] = "empty"
        headers["Sec-Fetch-Site"] = "same-origin"
        headers["X-Requested-With"] = "XMLHttpRequest"

        self.request_count += 1
        if self.request_count > 1 and self.request_count % self.pause_every == 0:
            pause = _jitter(self.pause_duration, 5.0)
            print(f"      [stealth] natural pause {pause:.0f}s...")
            time.sleep(pause)

        r = self.session.get(
            url,
            params=params,
            headers=headers,
            timeout=15,
        )
        self.last_url = r.url
        time.sleep(_jitter(self.base_delay, 1.5))

        try:
            return r.json()
        except Exception:
            return {}

    def reset(self):
        """Reset session — use between different sites."""
        self.session      = requests.Session()
        self.last_url     = None
        self.request_count = 0


def shuffled_members(members: list, seed: Optional[int] = None) -> list:
    """
    Return members in a randomised order.
    Using today's date as seed means order changes daily but is
    reproducible if the run needs to be restarted.
    """
    if seed is None:
        seed = int(datetime.date.today().strftime("%Y%m%d"))
    rng = random.Random(seed)
    shuffled = list(members)
    rng.shuffle(shuffled)
    return shuffled


# ── CONVENIENCE FUNCTION ──────────────────────────────────────────────────────

def make_session(homepage: str, base_delay: float = 4.0) -> StealthSession:
    """
    Create and warm up a StealthSession for a given site.
    Returns the ready-to-use session.
    """
    s = StealthSession(base_delay=base_delay)
    s.warm_up(homepage)
    return s
