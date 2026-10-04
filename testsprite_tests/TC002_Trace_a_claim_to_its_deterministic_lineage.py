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
        
        # -> Click the 'Evidence' link in the left navigation to open the Evidence page.
        # Evidence link
        elem = page.get_by_role("link", name="Evidence")
        await elem.click(timeout=10000)
        
        # -> Click the 'View DAG' button for the claim 'CutMix reduces out-of-distribution error by 8.7% compared to baseline' to open its lineage view.
        # View DAG button
        elem = page.get_by_role("row", name="clm_c760853d44a5 CutMix").get_by_role("button")
        await elem.click(timeout=10000)
        
        # --> Assertions to verify final state
        
        # --> Lineage view is open for claim clm_c760853d44a5.
        # Assert-outcome: passed
        # Assert: The browser URL contains the claim lineage path.
        await expect(page).to_have_url(re.compile("/evidence/lineage/clm_c760853d44a5"), timeout=15000), "The browser URL contains the claim lineage path."
        
        # --> Claim node for clm_c760853d44a5 is visible with its claim text.
        # Assert-outcome: passed
        # Assert: The Claim node displays the claim text.
        await expect(page.get_by_role("main").nth(0)).to_contain_text("CutMix reduces out-of-distribution error by 8.7% compared to", timeout=15000), "The Claim node displays the claim text."
        
        # --> Result node res_0e6e0c9cbf19 is visible showing its ood_error detail.
        # Assert-outcome: passed
        # Assert: The Result node shows the ood_error value.
        await expect(page.locator("xpath=/html/body/div[1]/div/div/main/div/div/div[2]/div[2]/div[2]/div[3]/div[3]").nth(0)).to_have_text("ood_error: 0.198", timeout=15000), "The Result node shows the ood_error value."
        
        # --> Execution node exec_212901cd4573 is visible with completed status and exit code.
        # Assert-outcome: passed
        # Assert: The Execution node shows completion status and exit code.
        await expect(page.locator("xpath=/html/body/div[1]/div/div/main/div/div/div[2]/div[2]/div[3]/div[3]/div[3]").nth(0)).to_have_text("Status: completed, Exit code: 0", timeout=15000), "The Execution node shows completion status and exit code."
        
        # --> Experiment node exp_e6721c220982 is visible with its variant text.
        # Assert-outcome: passed
        # Assert: The Experiment node displays the experiment variant text.
        await expect(page.get_by_role("main").nth(0)).to_contain_text("CutMix Alpha 1.0 Variant", timeout=15000), "The Experiment node displays the experiment variant text."
        await asyncio.sleep(5)

    finally:
        if context:
            await context.close()
        if browser:
            await browser.close()
        if pw:
            await pw.stop()

asyncio.run(run_test())
    