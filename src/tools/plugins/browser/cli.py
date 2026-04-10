"""Browser automation CLI — manual testing interface.

Usage:
    python cli.py navigate --url https://www.linkedin.com
    python cli.py text --selector "body"
    python cli.py click --selector "button#submit"
    python cli.py fill --selector "#email" --value "test@example.com"
    python cli.py screenshot
    python cli.py info
    python cli.py close

Requires: playwright install chromium
"""

import argparse
import asyncio
import json


async def run_navigate(args):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        resp = await page.goto(args.url, wait_until="domcontentloaded")
        print(json.dumps({
            "url": page.url,
            "title": await page.title(),
            "status": resp.status if resp else "unknown",
        }, indent=2))
        if args.wait:
            text = await page.inner_text("body")
            print(f"\n--- Page text ({len(text)} chars) ---")
            print(text[:2000])
        await browser.close()


async def run_text(args):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(args.url, wait_until="domcontentloaded")
        text = await page.inner_text(args.selector)
        print(text[:3000])
        await browser.close()


async def run_info(args):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(args.url, wait_until="domcontentloaded")
        elements = await page.eval_on_selector_all(
            "input, textarea, select, button, a[href]",
            """els => els.slice(0, 20).map(el => ({
                tag: el.tagName, type: el.type || '', name: el.name || '',
                id: el.id || '', placeholder: el.placeholder || '',
                text: (el.textContent || '').trim().substring(0, 60),
            }))""",
        )
        print(json.dumps({"url": page.url, "title": await page.title(), "elements": elements}, indent=2))
        await browser.close()


async def run_screenshot(args):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(args.url, wait_until="domcontentloaded")
        await page.screenshot(path=args.output)
        print(f"Screenshot saved to {args.output}")
        await browser.close()


def main():
    parser = argparse.ArgumentParser(description="Browser automation CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("navigate", help="Navigate to a URL")
    p.add_argument("--url", required=True)
    p.add_argument("--wait", action="store_true", help="Print page text after navigation")

    p = sub.add_parser("text", help="Extract text from a page")
    p.add_argument("--url", required=True)
    p.add_argument("--selector", default="body")

    p = sub.add_parser("info", help="Get page interactive elements")
    p.add_argument("--url", required=True)

    p = sub.add_parser("screenshot", help="Take a screenshot")
    p.add_argument("--url", required=True)
    p.add_argument("--output", default="screenshot.png")

    args = parser.parse_args()

    if args.command == "navigate":
        asyncio.run(run_navigate(args))
    elif args.command == "text":
        asyncio.run(run_text(args))
    elif args.command == "info":
        asyncio.run(run_info(args))
    elif args.command == "screenshot":
        asyncio.run(run_screenshot(args))


if __name__ == "__main__":
    main()
