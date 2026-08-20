"""Diagnose what the runner actually receives from app.kiro.dev/signin.

Runs a bare Playwright Chromium (NO stealth, NO fingerprint script) and dumps
the raw HTML / title / URL so we can see whether the empty "Kiro Web Portal"
page is an AWS bot-challenge interstitial or a genuinely broken render.
"""
import asyncio
import sys

URL = "https://app.kiro.dev/signin?state=diag&code_challenge=abc&code_challenge_method=S256&redirect_uri=http%3A%2F%2F127.0.0.1%3A3128&redirect_from=KiroIDE"


async def main():
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page()
        resp = await page.goto(URL, timeout=60000, wait_until="domcontentloaded")
        print("=== STATUS ===")
        print(resp.status if resp else "None")
        print("=== FINAL URL ===")
        print(page.url)
        print("=== TITLE ===")
        print(await page.title())
        await page.wait_for_timeout(5000)
        print("=== TITLE after 5s ===")
        print(await page.title())
        print("=== BODY TEXT ===")
        body = await page.evaluate("() => document.body ? document.body.innerText : ''")
        print(repr(body[:500]))
        print("=== META/SCRIPTS ===")
        meta = await page.evaluate("""() => {
            const metas = Array.from(document.querySelectorAll('meta')).map(m => m.outerHTML);
            const scripts = Array.from(document.querySelectorAll('script')).map(s => (s.src || 'INLINE').slice(0, 120));
            return {metas: metas.slice(0, 10), scripts: scripts.slice(0, 15)};
        }""")
        print(meta)
        html = await page.content()
        print("=== HTML LEN ===", len(html))
        print(html[:1500])
        await browser.close()


asyncio.run(main())