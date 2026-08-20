"""Diagnose v2: capture console errors + failed/slow network requests.

The signin HTML loads (406KB, correct <title>) but React never mounts (empty
body). This script pins down WHY — likely the CDN JS (assets.app.kiro.dev
vendor.js/main.js) fails or stalls on the runner.
"""
import asyncio

URL = "https://app.kiro.dev/signin?state=diag&code_challenge=abc&code_challenge_method=S256&redirect_uri=http%3A%2F%2F127.0.0.1%3A3128&redirect_from=KiroIDE"


async def main():
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page()

        console_msgs = []
        failed_reqs = []
        slow_reqs = []

        page.on("console", lambda m: console_msgs.append(f"[{m.type}] {m.text[:200]}"))
        page.on("requestfailed", lambda r: failed_reqs.append(f"{r.url[:120]} :: {r.failure}"))
        page.on("response", lambda r: slow_reqs.append(f"{r.status} {r.url[:120]}") if r.status >= 400 else None)

        try:
            await page.goto(URL, timeout=90000, wait_until="domcontentloaded")
        except Exception as e:
            print("=== GOTO ERROR ===", e)

        for i in range(6):
            await page.wait_for_timeout(5000)
            title = await page.title()
            body_len = await page.evaluate("() => document.body ? document.body.innerText.length : -1")
            btn_count = await page.evaluate("() => document.querySelectorAll('button').length")
            print(f"=== t+{(i+1)*5}s title={title!r} bodyLen={body_len} btns={btn_count} ===")

        print("=== CONSOLE (last 40) ===")
        for m in console_msgs[-40:]:
            print(m)
        print("=== FAILED REQUESTS ===")
        for f in failed_reqs:
            print(f)
        print("=== HTTP >=400 ===")
        for s in slow_reqs:
            print(s)
        print("=== FINAL URL ===", page.url)
        await browser.close()


asyncio.run(main())