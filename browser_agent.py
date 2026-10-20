"""
Playwright Headless Browser Agent for LLM Clip Harvester.
Responsibilities:
1. Search YouTube for search queries with optional 4K filter (sp=EgJwAQ%253D%253D)
   and extract the top matching video URL and title.
2. Handle Google/YouTube consent and cookie banners.
3. Export Netscape-formatted cookie file for yt-dlp bot bypass.
4. Verify video metadata and availability.
"""

import os
import re
import tempfile
import urllib.parse
from typing import Callable, Dict, List, Optional, Tuple


def check_playwright_available() -> Tuple[bool, str]:
    """Check if Playwright package and Chromium browser are installed and ready."""
    try:
        import playwright
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False, "Playwright python package is not installed. Run 'pip install playwright'."

    try:
        with sync_playwright() as p:
            # Check executable path without full launch
            browser_type = p.chromium
            exe_path = browser_type.executable_path
            if not os.path.exists(exe_path):
                return False, f"Chromium browser binary missing. Run 'playwright install chromium'."
            return True, f"Playwright Chromium ready ({exe_path})"
    except Exception as e:
        return False, f"Playwright check failed: {str(e)}"


class BrowserAgent:
    """Thread-safe Playwright browser agent for search and session resolution."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )

    def _dismiss_consent(self, page, log_callback: Optional[Callable[[str], None]] = None) -> None:
        """Dismiss YouTube / Google consent popups if present."""
        consent_texts = [
            "Accept all", "Reject all", "I agree", "Agree",
            "Before you continue to YouTube", "Accept the use of cookies"
        ]
        for text in consent_texts:
            try:
                btn = page.locator(f"button:has-text('{text}')")
                if btn.count() > 0 and btn.first.is_visible():
                    if log_callback:
                        log_callback(f"[BrowserAgent] Dismissing consent dialog: '{text}'")
                    btn.first.click()
                    page.wait_for_timeout(1000)
                    break
            except Exception:
                pass

    def search_youtube_top_url(
        self,
        query: str,
        prefer_4k: bool = True,
        log_callback: Optional[Callable[[str], None]] = None
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Search YouTube for a query and return (video_url, video_title, video_id).
        If prefer_4k is True, applies YouTube's 4K filter (sp=EgJwAQ%253D%253D).
        Falls back to standard search if 4K search returns no videos.
        """
        from playwright.sync_api import sync_playwright

        encoded_query = urllib.parse.quote_plus(query.strip())
        
        # Build search URLs
        search_urls = []
        if prefer_4k:
            # 4K filter search first
            search_urls.append((f"https://www.youtube.com/results?search_query={encoded_query}&sp=EgJwAQ%253D%253D", True))
        search_urls.append((f"https://www.youtube.com/results?search_query={encoded_query}", False))

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context(
                user_agent=self.user_agent,
                viewport={"width": 1280, "height": 800},
                locale="en-US"
            )
            page = context.new_page()

            for target_url, is_4k in search_urls:
                mode_str = "4K Filtered" if is_4k else "Standard"
                if log_callback:
                    log_callback(f"[BrowserAgent] Searching YouTube ({mode_str}): '{query}'")

                try:
                    page.goto(target_url, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(2000)
                    self._dismiss_consent(page, log_callback)

                    # Extract video title anchor elements
                    # Selector looks for standard video item links in search results
                    links = page.locator("a#video-title").all()
                    
                    for link in links:
                        try:
                            href = link.get_attribute("href")
                            if href and "/watch?v=" in href:
                                title = link.get_attribute("title") or link.inner_text().strip()
                                # Clean href to base watch?v=...
                                clean_href = href.split("&")[0]
                                full_url = f"https://www.youtube.com{clean_href}"
                                
                                # Extract video ID
                                v_id_match = re.search(r"v=([a-zA-Z0-9_-]{11})", clean_href)
                                v_id = v_id_match.group(1) if v_id_match else None

                                if log_callback:
                                    log_callback(f"[BrowserAgent] Found: '{title}' ({full_url})")
                                browser.close()
                                return full_url, title, v_id
                        except Exception:
                            continue

                except Exception as e:
                    if log_callback:
                        log_callback(f"[BrowserAgent] Error during {mode_str} search: {e}")

            browser.close()

        if log_callback:
            log_callback(f"[BrowserAgent] No video found for query: '{query}'")
        return None, None, None

    def export_session_cookies_file(
        self,
        log_callback: Optional[Callable[[str], None]] = None
    ) -> Optional[str]:
        """
        Open YouTube session, solve consent/bypass challenges, and export cookies
        into a temporary Netscape-formatted cookie file that yt-dlp can use.
        Returns the absolute filepath to the exported cookie file.
        """
        from playwright.sync_api import sync_playwright

        if log_callback:
            log_callback("[BrowserAgent] Extracting fresh YouTube session cookies...")

        cookie_path = os.path.join(tempfile.gettempdir(), "yt_clip_harvester_cookies.txt")

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.headless)
                context = browser.new_context(
                    user_agent=self.user_agent,
                    viewport={"width": 1280, "height": 800},
                    locale="en-US"
                )
                page = context.new_page()
                page.goto("https://www.youtube.com", wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(2500)
                self._dismiss_consent(page, log_callback)

                cookies = context.cookies()
                browser.close()

            if not cookies:
                if log_callback:
                    log_callback("[BrowserAgent] No cookies extracted from session.")
                return None

            # Write cookies in Netscape format
            # Format: domain \t flag \t path \t secure \t expiration \t name \t value
            with open(cookie_path, "w", encoding="utf-8") as f:
                f.write("# Netscape HTTP Cookie File\n")
                f.write("# Exported by LLM Clip Harvester BrowserAgent\n\n")
                for c in cookies:
                    domain = c.get("domain", ".youtube.com")
                    flag = "TRUE" if domain.startswith(".") else "FALSE"
                    path = c.get("path", "/")
                    secure = "TRUE" if c.get("secure", False) else "FALSE"
                    expires = str(int(c.get("expires", -1))) if c.get("expires", -1) > 0 else "0"
                    name = c.get("name", "")
                    value = c.get("value", "")
                    f.write(f"{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}\n")

            if log_callback:
                log_callback(f"[BrowserAgent] Successfully exported {len(cookies)} cookies to {cookie_path}")
            return cookie_path

        except Exception as e:
            if log_callback:
                log_callback(f"[BrowserAgent] Failed to export cookies: {e}")
            return None
