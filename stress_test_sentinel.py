import os
import sys
import time
import json
import random
import logging
import subprocess
import tempfile
import shutil
from typing import Optional
from dataclasses import dataclass

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@dataclass
class BrowserSentinelArtifacts:
    token: str
    session_observer_token: Optional[str] = None
    passkey_capabilities: Optional[str] = None

# We need to find the chrome binary. We'll use a simplified version of _find_chrome_binary
def find_chrome_binary():
    # Common paths for different platforms
    if sys.platform == "darwin":
        paths = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
    elif sys.platform == "win32":
        paths = [
            os.path.expandvars("%ProgramFiles%\\Google\\Chrome\\Application\\chrome.exe"),
            os.path.expandvars("%ProgramFiles(x86)%\\Google\\Chrome\\Application\\chrome.exe"),
            os.path.expandvars("%LocalAppData%\\Google\\Chrome\\Application\\chrome.exe"),
        ]
    else:  # Linux
        paths = [
            "/usr/bin/google-chrome",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/opt/google/chrome/google-chrome",
        ]
    
    for path in paths:
        if os.path.exists(path):
            return path
    return None

def wait_for_cdp_ready(cdp_url, proc, timeout_seconds=20):
    import urllib.request
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            return False, "Process terminated"
        try:
            with urllib.request.urlopen(f"{cdp_url}/json/version", timeout=1) as resp:
                if resp.status == 200:
                    return True, ""
        except Exception:
            pass
        time.sleep(0.5)
    return False, "Timeout waiting for CDP"

def get_sentinel_artifacts(page_url, headless=True, display=None):
    from playwright.sync_api import sync_playwright
    
    chrome_binary = find_chrome_binary()
    if not chrome_binary:
        return None, "Chrome binary not found"
    
    cdp_port = random.randint(10000, 20000)
    user_data_dir = tempfile.mkdtemp(prefix=f"sentinel-test-{cdp_port}-")
    cdp_url = f"http://127.0.0.1:{cdp_port}"
    
    env = os.environ.copy()
    if display:
        env["DISPLAY"] = display
        
    chrome_args = [
        chrome_binary,
        f"--remote-debugging-port={cdp_port}",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-blink-features=AutomationControlled",
        f"--user-data-dir={user_data_dir}",
        "about:blank",
    ]
    if headless:
        chrome_args.extend(["--headless=new", "--disable-gpu"])
    
    proc = None
    try:
        proc = subprocess.Popen(chrome_args, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ready, err = wait_for_cdp_ready(cdp_url, proc)
        if not ready:
            return None, f"CDP not ready: {err}"
            
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(cdp_url)
            context = browser.new_context()
            
            # Simple stealth
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            """)
            
            page = browser.new_pages[0] if browser.new_pages else context.new_page()
            
            start_time = time.time()
            try:
                # Visit the page
                response = page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
                status = response.status if response else "no-response"
                logger.info(f"Visited {page_url}, status: {status}")
                
                # Evaluate Sentinel
                script = """
                async () => {
                  const waitForSdk = async () => {
                    const deadline = Date.now() + 15000;
                    while (Date.now() < deadline) {
                      if (window.SentinelSDK && typeof window.SentinelSDK.token === 'function') {
                        return window.SentinelSDK;
                      }
                      await new Promise((resolve) => setTimeout(resolve, 500));
                    }
                    throw new Error('SentinelSDK not ready');
                  };

                  const ensureSdk = async () => {
                    if (window.SentinelSDK && typeof window.SentinelSDK.token === 'function') {
                      return window.SentinelSDK;
                    }
                    const existing = Array.from(document.scripts).find((item) => {
                      const src = String(item.src || '');
                      return src.includes('sentinel.openai.com/backend-api/sentinel/sdk.js')
                        || src.includes('sentinel.openai.com/sentinel/');
                    });
                    const src = existing && existing.src ? existing.src : 'https://sentinel.openai.com/backend-api/sentinel/sdk.js';
                    
                    await new Promise((resolve, reject) => {
                      const tag = document.createElement('script');
                      tag.src = src;
                      tag.async = true;
                      tag.onload = () => resolve();
                      tag.onerror = () => reject(new Error('load sentinel sdk failed'));
                      document.head.appendChild(tag);
                    });
                    return waitForSdk();
                  };

                  const sdk = await ensureSdk();
                  const token = await sdk.token('password_verify');
                  const origin = window.location.origin;
                  return { token, origin };
                }
                """
                result = page.evaluate(script)
                duration = time.time() - start_time
                return result, f"Success ({duration:.2f}s)"
            except Exception as e:
                return None, f"Error: {str(e)}"
            finally:
                context.close()
                browser.close()
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except:
                proc.kill()
        shutil.rmtree(user_data_dir, ignore_errors=True)

def run_test():
    urls = [
        "https://auth.openai.com/create-account/password",
        "https://sentinel.openai.com/backend-api/sentinel/frame.html"
    ]
    headless_modes = [True] # Default to True, False if DISPLAY is available
    
    # Check if we can test headless=False
    # On Linux we might have DISPLAY=:99
    # On macOS we can run it normally if we are in a GUI session.
    # But for "container" test, we'll try to use DISPLAY=:99 if it exists.
    
    test_display = os.environ.get("DISPLAY")
    if not test_display:
        # Try to find if Xvfb is running on :99
        if os.path.exists("/tmp/.X11-unix/X99"):
            test_display = ":99"
    
    if test_display:
        headless_modes.append(False)
        logger.info(f"Adding headless=False test using DISPLAY={test_display}")
    else:
        logger.warning("No DISPLAY found, skipping headless=False test")

    results = {}
    for url in urls:
        for hl in headless_modes:
            results[(url, hl)] = {"success": 0, "total": 0, "errors": [], "origins": set()}

    num_iterations = 10
    for i in range(num_iterations):
        logger.info(f"--- Iteration {i+1}/{num_iterations} ---")
        for url in urls:
            for hl in headless_modes:
                display = test_display if not hl else None
                logger.info(f"Testing URL: {url}, Headless: {hl}")
                res, msg = get_sentinel_artifacts(url, headless=hl, display=display)
                
                key = (url, hl)
                results[key]["total"] += 1
                if res and res.get("token"):
                    results[key]["success"] += 1
                    results[key]["origins"].add(res.get("origin"))
                    logger.info(f"Result: {msg}, Token prefix: {res['token'][:20]}...")
                else:
                    results[key]["errors"].append(msg)
                    logger.error(f"Result: {msg}")

    # Final Output
    print("\n" + "="*50)
    print("STRESS TEST RESULTS")
    print("="*50)
    
    best_config = None
    max_rate = -1

    for (url, hl), stats in results.items():
        rate = (stats["success"] / stats["total"]) * 100
        print(f"URL: {url}")
        print(f"Headless: {hl}")
        print(f"Success Rate: {rate:.1f}% ({stats['success']}/{stats['total']})")
        print(f"Origins: {list(stats['origins'])}")
        if stats["errors"]:
            # Print unique errors
            unique_errors = list(set(stats["errors"]))
            print(f"Errors: {unique_errors[:3]}...")
        print("-" * 30)
        
        if rate > max_rate:
            max_rate = rate
            best_config = (url, hl)
        elif rate == max_rate:
            # Prefer frame.html if rates are same, as it's lighter
            if "frame.html" in url:
                best_config = (url, hl)

    print("="*50)
    if best_config:
        url, hl = best_config
        print(f"RECOMMENDED CONFIG:")
        print(f"URL: {url}")
        print(f"Headless Mode: {hl}")
        print(f"Stealth: Default + JS SDK manual loading")
    print("="*50)

if __name__ == "__main__":
    run_test()
