"""
Renova cookies do Instagram e TikTok automaticamente via Playwright.
Executado via cron job semanal no VPS.

Configuração no crontab:
    0 3 * * 0 cd /app && /app/venv/bin/python scripts/refresh_cookies.py >> /var/log/cookie-refresh.log 2>&1

Variáveis de ambiente necessárias (.env):
    INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD
    TIKTOK_USERNAME, TIKTOK_PASSWORD  (opcionais)
"""

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

COOKIES_DIR = ROOT_DIR / "config" / "cookies"


def format_netscape(cookies: list[dict]) -> str:
    lines = ["# Netscape HTTP Cookie File", ""]
    for c in cookies:
        domain = c.get("domain", "")
        flag = "TRUE" if domain.startswith(".") else "FALSE"
        path = c.get("path", "/")
        secure = "TRUE" if c.get("secure") else "FALSE"
        expires = int(c.get("expires", 0)) if c.get("expires") else 0
        name = c.get("name", "")
        value = c.get("value", "")
        lines.append(f"{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}")
    return "\n".join(lines)


async def refresh_instagram() -> None:
    username = os.environ.get("INSTAGRAM_USERNAME")
    password = os.environ.get("INSTAGRAM_PASSWORD")
    if not username or not password:
        print("Instagram: INSTAGRAM_USERNAME/PASSWORD não configurados, pulando.")
        return

    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto("https://www.instagram.com/accounts/login/")
        await page.fill('input[name="username"]', username)
        await page.fill('input[name="password"]', password)
        await page.click('button[type="submit"]')
        await page.wait_for_url("https://www.instagram.com/", timeout=15000)

        cookies = await context.cookies()
        ig_cookies = [c for c in cookies if "instagram.com" in c.get("domain", "")]

        out = COOKIES_DIR / "www.instagram.com_cookies.txt"
        out.write_text(format_netscape(ig_cookies))
        print(f"Instagram: {len(ig_cookies)} cookies salvos em {out}")
        await browser.close()


async def refresh_tiktok() -> None:
    username = os.environ.get("TIKTOK_USERNAME")
    password = os.environ.get("TIKTOK_PASSWORD")
    if not username or not password:
        print("TikTok: TIKTOK_USERNAME/PASSWORD não configurados, pulando.")
        return

    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto("https://www.tiktok.com/login/phone-or-email/email")
        await page.fill('input[name="username"]', username)
        await page.fill('input[type="password"]', password)
        await page.click('button[type="submit"]')
        await page.wait_for_timeout(5000)

        cookies = await context.cookies()
        tt_cookies = [c for c in cookies if "tiktok.com" in c.get("domain", "")]

        out = COOKIES_DIR / "www.tiktok.com_cookies.txt"
        out.write_text(format_netscape(tt_cookies))
        print(f"TikTok: {len(tt_cookies)} cookies salvos em {out}")
        await browser.close()


async def main() -> None:
    COOKIES_DIR.mkdir(parents=True, exist_ok=True)
    await refresh_instagram()
    await refresh_tiktok()
    print("Cookie refresh concluído.")


if __name__ == "__main__":
    asyncio.run(main())
