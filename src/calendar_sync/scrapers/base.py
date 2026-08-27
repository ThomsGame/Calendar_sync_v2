"""Base browser manager and shared page utilities."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from loguru import logger
from playwright.async_api import Browser, BrowserContext, Frame, Page, async_playwright

# Common launch args for Chromium in WSL / headless environments
_CHROMIUM_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--ignore-certificate-errors",
    "--disable-blink-features=AutomationControlled",
]


class BrowserManager:
    """Manages Playwright browser instances."""

    def __init__(self, headless: bool = True, user_data_dir: Optional[str] = None):
        self.headless = headless
        self.user_data_dir = user_data_dir
        self._playwright = None
        self._browser: Optional[Browser | BrowserContext] = None

    async def launch(self) -> Browser | BrowserContext:
        """Launch a new browser instance."""
        self._playwright = await async_playwright().start()

        if self.user_data_dir:
            # Persistent context: first arg is the user data dir path
            path = Path(self.user_data_dir)
            path.mkdir(parents=True, exist_ok=True)
            self._browser = await self._playwright.chromium.launch_persistent_context(
                str(path),
                headless=self.headless,
                args=_CHROMIUM_ARGS,
            )
            logger.debug(f"[BROWSER] Launched persistent context at {path}")
        else:
            self._browser = await self._playwright.chromium.launch(
                headless=self.headless,
                args=_CHROMIUM_ARGS,
            )
            logger.debug("[BROWSER] Launched fresh browser instance.")

        return self._browser

    async def new_page(self) -> Page:
        """Create a new page in the browser."""
        if not self._browser:
            await self.launch()
        return await self._browser.new_page()

    async def close(self) -> None:
        """Close browser and cleanup."""
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None


async def find_calendar_frame(page: Page) -> Optional[Frame]:
    """Find the FullCalendar iframe in Snexi page."""
    for frame in page.frames:
        if "indisponibilites" in frame.url:
            return frame
    # Fallback: check for FullCalendar elements
    for frame in page.frames:
        try:
            has_calendar = await frame.evaluate(
                "!!document.querySelector('.fc-event, .fc-time-grid-event, .fc-day-grid-event')"
            )
            if has_calendar:
                return frame
        except Exception:
            pass
    return None


async def wait_for_calendar_frame(page: Page, timeout_ms: int = 8000) -> Optional[Frame]:
    """Wait for calendar iframe to appear."""
    import asyncio

    deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
    while asyncio.get_event_loop().time() < deadline:
        frame = await find_calendar_frame(page)
        if frame:
            return frame
        await asyncio.sleep(0.5)
    return None
