"""Browser-backed Sentinel token helpers for registration-critical flows."""

from __future__ import annotations

import json
import logging
import random
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Optional

from .browser_bind import _find_chrome_binary, _wait_for_cloudflare, wait_for_cdp_ready


logger = logging.getLogger(__name__)

_DEFAULT_PAGE_URL = "https://auth.openai.com/create-account/password"
class BrowserSentinelError(RuntimeError):
    """Raised when a browser-backed Sentinel token cannot be minted."""


@dataclass
class BrowserSentinelArtifacts:
    """Sentinel artifacts returned by the browser SDK."""

    token: str
    session_observer_token: Optional[str] = None
    passkey_capabilities: Optional[str] = None


def _infer_locale(accept_language: Optional[str]) -> str:
    text = str(accept_language or "").strip()
    if not text:
        return "en-US"
    primary = text.split(",", 1)[0].split(";", 1)[0].strip()
    return primary or "en-US"


def _build_auth_cookie_items(device_id: str) -> list[dict]:
    return [
        {
            "name": "oai-did",
            "value": str(device_id or "").strip(),
            "domain": ".auth.openai.com",
            "path": "/",
            "httpOnly": False,
            "secure": True,
            "sameSite": "Lax",
        }
    ]


def _chrome_args(
    chrome_binary: str,
    cdp_port: int,
    user_data_dir: str,
    proxy: Optional[str],
    headless: bool,
) -> list[str]:
    args = [
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
        "--window-size=1366,900",
        f"--user-data-dir={user_data_dir}",
        "about:blank",
    ]
    if headless:
        args.extend(["--headless=new", "--disable-gpu"])
    else:
        args.extend(
            [
                "--use-gl=angle",
                "--use-angle=swiftshader-webgl",
                "--enable-unsafe-swiftshader",
            ]
        )
    if proxy:
        args.append(f"--proxy-server={proxy}")
    return args


def _evaluate_sentinel(page, flow: str, include_session_observer: bool, include_passkey_capabilities: bool) -> dict:
    script = """
async ({ flow, includeSessionObserver, includePasskeyCapabilities }) => {
  const waitForSdk = async () => {
    const deadline = Date.now() + 20000;
    while (Date.now() < deadline) {
      if (window.SentinelSDK && typeof window.SentinelSDK.token === 'function') {
        return window.SentinelSDK;
      }
      await new Promise((resolve) => setTimeout(resolve, 250));
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
      tag.onload = resolve;
      tag.onerror = () => reject(new Error('load sentinel sdk failed'));
      document.head.appendChild(tag);
    });
    return waitForSdk();
  };

  const getPasskeyCapabilities = async () => {
    if (!includePasskeyCapabilities || typeof window.PublicKeyCredential === 'undefined') {
      return null;
    }
    try {
      if (typeof window.PublicKeyCredential.getClientCapabilities === 'function') {
        return await window.PublicKeyCredential.getClientCapabilities();
      }
    } catch (_error) {}

    const fallback = {};
    try {
      if (typeof window.PublicKeyCredential.isConditionalMediationAvailable === 'function') {
        fallback.conditionalGet = await window.PublicKeyCredential.isConditionalMediationAvailable();
      }
    } catch (_error) {}
    try {
      if (typeof window.PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable === 'function') {
        const available = await window.PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable();
        fallback.userVerifyingPlatformAuthenticator = available;
        fallback.passkeyPlatformAuthenticator = available;
      }
    } catch (_error) {}
    return Object.keys(fallback).length ? fallback : null;
  };

  const sdk = await ensureSdk();
  if (typeof sdk.init === 'function') {
    try {
      await sdk.init(flow);
    } catch (_error) {}
  }

  const token = await Promise.race([
    sdk.token(flow),
    new Promise((_, reject) => setTimeout(() => reject(new Error('Sentinel token timeout')), 25000)),
  ]);
  const sessionObserverToken = includeSessionObserver && typeof sdk.sessionObserverToken === 'function'
    ? await sdk.sessionObserverToken(flow)
    : null;
  const passkeyCapabilities = await getPasskeyCapabilities();

  return { token, sessionObserverToken, passkeyCapabilities };
}
"""
    return page.evaluate(
        script,
        {
            "flow": flow,
            "includeSessionObserver": include_session_observer,
            "includePasskeyCapabilities": include_passkey_capabilities,
        },
    )


def fetch_browser_sentinel_artifacts(
    *,
    flow: str,
    device_id: str,
    page_url: str = _DEFAULT_PAGE_URL,
    proxy: Optional[str] = None,
    user_agent: Optional[str] = None,
    accept_language: Optional[str] = None,
    include_session_observer: bool = False,
    include_passkey_capabilities: bool = False,
    headless: bool = True,
    timeout_seconds: int = 45,
) -> BrowserSentinelArtifacts:
    """Use a real browser SDK execution path to mint Sentinel artifacts."""

    chrome_binary = _find_chrome_binary()
    if not chrome_binary:
        raise BrowserSentinelError("chrome binary not found")

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise BrowserSentinelError("playwright not installed") from exc

    cdp_port = random.randint(9481, 9720)
    user_data_dir = tempfile.mkdtemp(prefix=f"codex-sentinel-{cdp_port}-")
    cdp_url = f"http://127.0.0.1:{cdp_port}"
    chrome_proc = None

    try:
        chrome_proc = subprocess.Popen(
            _chrome_args(chrome_binary, cdp_port, user_data_dir, proxy, headless),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        cdp_ready, cdp_stderr = wait_for_cdp_ready(cdp_url, chrome_proc, timeout_seconds=min(timeout_seconds, 20))
        if not cdp_ready:
            if cdp_stderr:
                logger.warning("browser sentinel cdp ready check failed: %s", cdp_stderr)
            raise BrowserSentinelError("chrome cdp port not responding")

        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(cdp_url)
            context = browser.new_context(
                viewport={"width": 1366, "height": 900},
                user_agent=str(user_agent or "").strip() or None,
                locale=_infer_locale(accept_language),
            )
            try:
                context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                )
                context.add_cookies(_build_auth_cookie_items(device_id))

                page = context.new_page()
                page.set_default_timeout(max(30000, int(timeout_seconds) * 1000))
                page.goto(str(page_url or _DEFAULT_PAGE_URL), wait_until="domcontentloaded", timeout=60000)
                cf_ok, cf_note = _wait_for_cloudflare(page, max_wait_seconds=min(timeout_seconds, 90))
                if not cf_ok:
                    raise BrowserSentinelError(cf_note or "cloudflare challenge not passed")

                payload = _evaluate_sentinel(
                    page,
                    flow=flow,
                    include_session_observer=include_session_observer,
                    include_passkey_capabilities=include_passkey_capabilities,
                )
            finally:
                context.close()
                browser.close()

        token = str((payload or {}).get("token") or "").strip()
        if not token:
            raise BrowserSentinelError("empty sentinel token")

        passkey_capabilities = (payload or {}).get("passkeyCapabilities")
        if passkey_capabilities is not None:
            passkey_capabilities = json.dumps(passkey_capabilities, separators=(",", ":"), ensure_ascii=False)

        session_observer_token = str((payload or {}).get("sessionObserverToken") or "").strip() or None
        return BrowserSentinelArtifacts(
            token=token,
            session_observer_token=session_observer_token,
            passkey_capabilities=passkey_capabilities,
        )
    except BrowserSentinelError:
        raise
    except Exception as exc:
        logger.warning("browser sentinel mint failed: %s", exc)
        raise BrowserSentinelError(str(exc) or "browser sentinel mint failed") from exc
    finally:
        if chrome_proc is not None:
            try:
                chrome_proc.terminate()
                chrome_proc.wait(timeout=4)
            except Exception:
                try:
                    chrome_proc.kill()
                except Exception:
                    pass
        shutil.rmtree(user_data_dir, ignore_errors=True)
