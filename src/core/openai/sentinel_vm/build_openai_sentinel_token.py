#!/usr/bin/env python3
import json
import os
import subprocess
import tempfile
import logging
from pathlib import Path
from urllib.request import Request, urlopen

import requests
import re

logger = logging.getLogger(__name__)

def fetch_current_sentinel_version() -> str:
    """从 ChatGPT 首页动态探测最新的 Sentinel SDK 版本"""
    try:
        # 探测首页以获取 sdk.js 链接
        resp = requests.get("https://chatgpt.com/", timeout=10)
        if resp.status_code == 200:
            # 匹配类似 https://sentinel.openai.com/sentinel/20260219f9f6/sdk.js
            match = re.search(r"sentinel/([a-f0-9]+)/sdk\.js", resp.text)
            if match:
                version = match.group(1)
                logger.info(f"探测到最新 Sentinel 版本: {version}")
                return version
    except Exception as e:
        logger.warning(f"动态版本探测失败: {e}")
    
    # 默认兜底版本
    return "20260219f9f6"

SENTINEL_VERSION = fetch_current_sentinel_version()
SENTINEL_SDK_URL = f"https://sentinel.openai.com/sentinel/{SENTINEL_VERSION}/sdk.js"
SENTINEL_REQ_URL = "https://sentinel.openai.com/backend-api/sentinel/req"

ROOT_DIR = Path(__file__).resolve().parent
NODE_VM_FILE = ROOT_DIR / "openai_sentinel_vm.js"
SDK_CACHE_DIR = Path(tempfile.gettempdir()) / "codex-console" / "sentinel" / SENTINEL_VERSION
SDK_CACHE_FILE = SDK_CACHE_DIR / "sdk.js"


def resolve_node_binary() -> str:
    from shutil import which

    node_binary = which("node")
    if not node_binary:
        raise RuntimeError("node not found. Please install Node.js to use the Sentinel VM.")
    return node_binary


def ensure_sdk_file() -> Path:
    SDK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if SDK_CACHE_FILE.exists() and SDK_CACHE_FILE.stat().st_size > 0:
        return SDK_CACHE_FILE

    request = Request(
        SENTINEL_SDK_URL,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://auth.openai.com/",
            "Accept": "*/*",
        },
    )
    with urlopen(request, timeout=20) as response:
        SDK_CACHE_FILE.write_bytes(response.read())
    return SDK_CACHE_FILE


def node_environment(device_id: str, user_agent: str) -> dict:
    return {
        "device_id": device_id,
        "user_agent": user_agent,
        "language": "zh-CN",
        "languages": ["zh-CN", "zh"],
        "hardware_concurrency": 12,
        "screen_width": 1366,
        "screen_height": 768,
        "performance_now": 12345.67,
        "time_origin": 1710000000000.0,
        "js_heap_size_limit": 4294967296,
    }


def run_node_vm(action: str, payload: dict) -> dict:
    node_binary = resolve_node_binary()
    sdk_file = ensure_sdk_file()
    full_payload = {
        "action": action,
        "sdk_path": str(sdk_file),
        **payload,
    }
    process = subprocess.run(
        [node_binary, str(NODE_VM_FILE)],
        input=json.dumps(full_payload, separators=(",", ":")),
        text=True,
        capture_output=True,
        cwd=str(ROOT_DIR),
        timeout=40,
        check=False,
    )
    if process.returncode != 0:
        raise RuntimeError(process.stderr.strip() or process.stdout.strip() or f"node exit={process.returncode}")
    if not process.stdout.strip():
        raise RuntimeError("node vm returned empty stdout")
    return json.loads(process.stdout)


def fetch_challenge(session: requests.Session, device_id: str, flow: str, request_p: str, user_agent: str) -> dict:
    body = {
        "p": request_p,
        "id": device_id,
        "flow": flow,
    }
    headers = {
        "Content-Type": "text/plain;charset=UTF-8",
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Referer": f"https://sentinel.openai.com/backend-api/sentinel/frame.html?sv={SENTINEL_VERSION}",
        "Origin": "https://sentinel.openai.com",
        "User-Agent": user_agent,
        "Accept-Language": "zh-CN,zh;q=0.9",
        "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Priority": "u=1, i",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    
    proxy = None
    # 尝试从 session 中提取代理
    if hasattr(session, 'proxies') and session.proxies:
        proxy = session.proxies

    response = requests.post(SENTINEL_REQ_URL, data=json.dumps(body), headers=headers, timeout=20, proxies=proxy)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("challenge response is not a JSON object")
    return payload


def build_sentinel_token(session, device_id: str, flow: str = "username_password_create", user_agent: str = None) -> str:
    """使用 Node VM 方案生成包含正确的 `t` 的 Sentinel Token"""
    
    if not user_agent:
        user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/146.0.0.0 Safari/537.36"
        )
        
    try:
        request_payload = run_node_vm("requirements", node_environment(device_id, user_agent))
        request_p = str(request_payload.get("request_p") or "").strip()
        if not request_p:
            raise RuntimeError("missing request_p")

        challenge = fetch_challenge(session, device_id=device_id, flow=flow, request_p=request_p, user_agent=user_agent)
        c_value = str(challenge.get("token") or "").strip()
        if not c_value:
            raise RuntimeError("challenge token is empty")

        solved = run_node_vm(
            "solve",
            {
                **node_environment(device_id, user_agent),
                "request_p": request_p,
                "challenge": challenge,
            },
        )
        final_p = str(solved.get("final_p") or solved.get("p") or "").strip()
        t_value = solved.get("t")
        if not final_p:
            raise RuntimeError("missing final_p")

        token = {
            "p": final_p,
            "t": "" if t_value is None else str(t_value),
            "c": c_value,
            "id": device_id,
            "flow": flow,
        }
        return json.dumps(token, separators=(",", ":"))
    except Exception as e:
        logger.error(f"Node VM Sentinel 生成失败: {e}")
        return None