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
        
        # -> Click the 'Research' link in the sidebar to open the Research list page.
        # Research link
        elem = page.get_by_role("link", name="Research")
        await elem.click(timeout=10000)
        
        # -> Click the 'New Research' button to open the create-investigation flow.
        # New Research button
        elem = page.get_by_role("button", name="New Research")
        await elem.click(timeout=10000)
        
        # -> Fill the 'Investigation Title' and 'Scientific Research Question' fields with a unique topic and click the 'Launch Research Run' button.
        # e.g., Vision Robustness to Out-of-Distribution... text field
        elem = page.get_by_role("textbox", name="e.g., Vision Robustness to")
        await elem.wait_for(state="visible", timeout=10000)
        await elem.fill("Automated test run 2026-10-04 UID-0001")
        
        # -> Fill the 'Investigation Title' and 'Scientific Research Question' fields with a unique topic and click the 'Launch Research Run' button.
        # e.g., Does CutMix augmentation significantly... text area
        elem = page.get_by_role("textbox", name="e.g., Does CutMix")
        await elem.wait_for(state="visible", timeout=10000)
        await elem.fill("Does a hypothetical augmentation improve model robustness on sample dataset? UID-0001")
        
        # -> Fill the 'Investigation Title' and 'Scientific Research Question' fields with a unique topic and click the 'Launch Research Run' button.
        # Launch Research Run button
        elem = page.get_by_role("button", name="Launch Research Run")
        await elem.click(timeout=10000)
        
        # -> Click the 'Research' link in the sidebar to open the Research list and verify the new run appears in the list.
        # Research link
        elem = page.get_by_role("complementary").get_by_role("link", name="Research")
        await elem.click(timeout=10000)
        
        # --> Assertions to verify final state
        
        # --> The new research run 'Automated test run 2026-10-04 UID-0001' appears in the Research Runs list.
        # Assert-outcome: passed
        # Assert: Verifies the new run title appears in the Research Runs table.
        await expect(page.locator("xpath=/html/body/div[1]/div/div/main/div/div/div[3]/table/tbody/tr/td[2]/div/div[1]").nth(0)).to_have_text("Automated test run 2026-10-04 UID-0001", timeout=15000), "Verifies the new run title appears in the Research Runs table."
        await asyncio.sleep(5)

    finally:
        if context:
            await context.close()
        if browser:
            await browser.close()
        if pw:
            await pw.stop()

asyncio.run(run_test())
    