"""Base browser manager and shared page utilities."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from loguru import logger
from playwright.async_api import Browser, BrowserContext, Page, async_playwright


class BrowserManager:
    """Manages Playwright browser instances."""

    def __init__(self, headless: bool = True, user_data_dir: Optional[str] = None):
        self.headless = headless
        self.user_data_dir = user_data_dir
        self._playwright = None
        self._browser: Optional[Browser] = None

    async def launch(self) -> Browser:
        """Launch a new browser instance."""
        self._playwright = await async_playwright().start()

        launch_args: dict = {
            "headless": self.headless,
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ],
        }

        if self.user_data_dir:
            path = Path(self.user_data_dir)
            path.mkdir(parents=True, exist_ok=True)
            launch_args["persistent_context"] = str(path)
            self._browser = await self._playwright.chromium.launch_persistent_context(
                **launch_args,
            )
        else:
            self._browser = await self._playwright.chromium.launch(**launch_args)

        return self._browser

    async def new_page(self) -> Page:
        """Create a new page in the browser."""
        if not self._browser:
            await self.launch()
        if isinstance(self._browser, BrowserContext):
            return await self._browser.new_page()
        return await self._browser.new_page()

    async def close(self) -> None:
        """Close browser and cleanup."""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
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
