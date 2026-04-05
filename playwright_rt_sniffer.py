import asyncio
import os
import json
from playwright.async_api import async_playwright

async def run():
    async with async_playwright() as p:
        # 使用 Playwright 自动找到的二进制路径
        browser = await p.chromium.launch(
            headless=False,  # 在 Xvfb 中有头启动
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--lang=en-US",
                "--password-store=basic"
            ]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        )
        
        page = await context.new_page()
        
        # 拦截所有响应，寻找 oaistb_rt_
        def on_response(response):
            headers = response.headers
            set_cookie = headers.get("set-cookie", "")
            if "oaistb" in set_cookie:
                print(f"\n🔥 [playwright-sniffer 捕获到 RT!] URL: {response.url}")
                print(f"Set-Cookie: {set_cookie}")
        
        page.on("response", on_response)
        
        print("[*] 浏览器已拉起，正在导航到 chatgpt.com...")
        try:
            await page.goto("https://chatgpt.com/", timeout=60000)
            print("[*] 页面加载完成。请在 VNC 界面进行注册操作。")
            print("[*] 嗅探器将运行 10 分钟...")
            await asyncio.sleep(600)
        except Exception as e:
            print(f"[!] 错误: {e}")
        finally:
            await browser.close()

if __name__ == "__main__":
    # 确保 DISPLAY 已设置
    os.environ.setdefault("DISPLAY", ":99")
    asyncio.run(run())
