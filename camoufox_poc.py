"""PoC: verify Camoufox installs/launches on GH Actions and renders Kiro signin.

Does NOT touch the main register flow — just proves the engine works and shows
what title/body/buttons Camoufox sees on app.kiro.dev/signin.
"""
import asyncio

URL = ("https://app.kiro.dev/signin?state=poc&code_challenge=abc"
       "&code_challenge_method=S256&redirect_uri=http%3A%2F%2F127.0.0.1%3A3128"
       "&redirect_from=KiroIDE")


async def main():
    from camoufox.async_api import AsyncCamoufox

    async with AsyncCamoufox(
        headless="virtual",       # Linux: virtual Xvfb display
        os="windows",
        humanize=True,
        block_webrtc=True,
        i_know_what_im_doing=True,
    ) as browser:
        page = await browser.new_page()
        await page.goto(URL, timeout=60000, wait_until="domcontentloaded")
        for i in range(4):
            await page.wait_for_timeout(3000)
            title = await page.title()
            body_len = await page.evaluate("() => document.body ? document.body.innerText.length : -1")
            webdriver = await page.evaluate("() => navigator.webdriver")
            ua = await page.evaluate("() => navigator.userAgent")
            print(f"t+{(i+1)*3}s title={title!r} bodyLen={body_len} webdriver={webdriver!r}", flush=True)
        btns = await page.evaluate(
            "() => Array.from(document.querySelectorAll('button')).map(b => (b.innerText||'').trim()).filter(t=>t)"
        )
        print(f"UA={ua}", flush=True)
        print(f"BUTTONS={btns}", flush=True)


asyncio.run(main())