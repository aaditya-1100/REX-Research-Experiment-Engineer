import asyncio
import re
from playwright import async_api
from playwright.async_api import expect

async def run_test():
    pw = None
    browser = None
    context = None

    try:
        # Start a Playwright session in asynchronous mode
        pw = await async_api.async_playwright().start()

        # Launch a Chromium browser in headless mode with custom arguments
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--window-size=1280,720",
                "--disable-dev-shm-usage",
                "--ipc=host",
                "--single-process"
            ],
        )

        # Create a new browser context (like an incognito window)
        context = await browser.new_context()
        # Wider default timeout to match the agent's DOM-stability budget;
        # auto-waiting Playwright APIs (expect, locator.wait_for) inherit this.
        context.set_default_timeout(15000)

        # Open a new page in the browser context
        page = await context.new_page()

        # Interact with the page elements to simulate user flow
        # -> navigate
        await page.goto("http://localhost:8000/")
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        
        # -> Click the 'Artifacts' link in the left sidebar to open the artifacts catalog.
        # Artifacts link
        elem = page.get_by_role("link", name="Artifacts")
        await elem.click(timeout=10000)
        
        # -> Click the 'All' filter button, then click the 'Verify' button for the first artifact to trigger hash verification.
        # all button
        elem = page.get_by_role("button", name="all")
        await elem.click(timeout=10000)
        
        # -> Click the 'All' filter button, then click the 'Verify' button for the first artifact to trigger hash verification.
        # Verify button
        elem = page.get_by_role("row", name="art_9f55e52b497e data/runs/").get_by_role("button")
        await elem.click(timeout=10000)
        
        # -> Click the 'Verify' button in the artifact details panel to run the physical disk verification and observe the resulting integrity status.
        # button
        elem = page.get_by_role("main").get_by_role("button").filter(has_text=re.compile(r"^$"))
        await elem.click(timeout=10000)
        
        # -> Click the 'Verify' button for the first artifact row (art_9f55e52b497e) to run file verification.
        # Verify button
        elem = page.get_by_role("row", name="art_9f55e52b497e data/runs/").get_by_role("button")
        await elem.click(timeout=10000)
        
        # --> Assertions to verify final state
        
        # --> An integrity status label is shown in the selected artifact's details.
        # Assert-outcome: passed
        # Assert: The artifact details panel displays an On-Disk Status label.
        await expect(page.get_by_role("main").nth(0)).to_contain_text("On-Disk Status", timeout=15000), "The artifact details panel displays an On-Disk Status label."
        
        # --> The artifact remains visible in the catalog and its details panel is accessible.
        await page.get_by_role("link", name="Download Raw File").nth(0).scroll_into_view_if_needed()
        # Assert-outcome: passed
        # Assert: The artifact's 'Download Raw File' link is visible in the details panel.
        await expect(page.get_by_role("link", name="Download Raw File").nth(0)).to_be_visible(timeout=15000), "The artifact's 'Download Raw File' link is visible in the details panel."
        await asyncio.sleep(5)

    finally:
        if context:
            await context.close()
        if browser:
            await browser.close()
        if pw:
            await pw.stop()

asyncio.run(run_test())
    