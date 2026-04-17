"""OpenAI Sentinel header builders backed by async PoW generation."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from typing import Any, Optional

from curl_cffi.requests import Session

from .sentinel import (
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_SENTINEL_DIFF,
    DEFAULT_SENTINEL_TOKEN_PREFIX,
    build_sentinel_config,
    build_sentinel_pow_token_async,
    solve_sentinel_pow_async,
)


SENTINEL_REQ_URL = "https://sentinel.openai.com/backend-api/sentinel/req"
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)
_DEFAULT_SEC_CH_UA = '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"'
_TOKEN_PREFIX_ENV = "OPENAI_SENTINEL_TOKEN_PREFIX"


def _resolve_token_prefix() -> str:
    prefix = os.getenv(_TOKEN_PREFIX_ENV, DEFAULT_SENTINEL_TOKEN_PREFIX).strip()
    return prefix or DEFAULT_SENTINEL_TOKEN_PREFIX


def _build_request_headers(
    device_id: str,
    user_agent: str,
    sec_ch_ua: Optional[str],
    extra_headers: Optional[dict[str, str]] = None,
) -> dict[str, str]:
    headers = {
        "Content-Type": "text/plain;charset=UTF-8",
        "Referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html",
        "Origin": "https://sentinel.openai.com",
        "User-Agent": user_agent,
        "sec-ch-ua": sec_ch_ua or _DEFAULT_SEC_CH_UA,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "oai-did": device_id,
        "X-OpenAI-Device-Id": device_id,
    }
    if extra_headers:
        headers.update(extra_headers)
    return headers


async def fetch_sentinel_challenge_async(
    session: Session,
    device_id: str,
    *,
    flow: str = "authorize_continue",
    user_agent: Optional[str] = None,
    sec_ch_ua: Optional[str] = None,
    impersonate: Optional[str] = None,
    extra_headers: Optional[dict[str, str]] = None,
) -> Optional[dict[str, Any]]:
    resolved_user_agent = str(user_agent or _DEFAULT_USER_AGENT).strip() or _DEFAULT_USER_AGENT
    request_body = json.dumps(
        {
            "p": await build_sentinel_pow_token_async(resolved_user_agent),
            "id": device_id,
            "flow": flow,
        }
    )
    request_kwargs: dict[str, Any] = {
        "data": request_body,
        "headers": _build_request_headers(device_id, resolved_user_agent, sec_ch_ua, extra_headers),
        "timeout": 20,
    }
    if impersonate:
        request_kwargs["impersonate"] = impersonate
    try:
        response = await asyncio.to_thread(session.post, SENTINEL_REQ_URL, **request_kwargs)
    except Exception:
        return None
    if response.status_code != 200:
        return None
    return response.json() if response.content else {}


def fetch_sentinel_challenge(
    session: Session,
    device_id: str,
    *,
    flow: str = "authorize_continue",
    user_agent: Optional[str] = None,
    sec_ch_ua: Optional[str] = None,
    impersonate: Optional[str] = None,
    extra_headers: Optional[dict[str, str]] = None,
) -> Optional[dict[str, Any]]:
    return _run_async_from_sync(
        lambda: fetch_sentinel_challenge_async(
            session,
            device_id,
            flow=flow,
            user_agent=user_agent,
            sec_ch_ua=sec_ch_ua,
            impersonate=impersonate,
            extra_headers=extra_headers,
        )
    )


async def _solve_challenge_pow_async(
    user_agent: str,
    seed: str,
    difficulty: str,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> str:
    config = build_sentinel_config(user_agent)
    solution = await solve_sentinel_pow_async(seed, difficulty, config, max_iterations=max_iterations)
    return f"{_resolve_token_prefix()}{solution}"


async def build_sentinel_token_async(
    session: Session,
    device_id: str,
    *,
    flow: str = "authorize_continue",
    user_agent: Optional[str] = None,
    sec_ch_ua: Optional[str] = None,
    impersonate: Optional[str] = None,
    extra_headers: Optional[dict[str, str]] = None,
) -> Optional[str]:
    resolved_device_id = str(device_id or "").strip()
    if not resolved_device_id:
        return None
    resolved_user_agent = str(user_agent or _DEFAULT_USER_AGENT).strip() or _DEFAULT_USER_AGENT
    challenge = await fetch_sentinel_challenge_async(
        session,
        resolved_device_id,
        flow=flow,
        user_agent=resolved_user_agent,
        sec_ch_ua=sec_ch_ua,
        impersonate=impersonate,
        extra_headers=extra_headers,
    )
    if not challenge:
        return None

    c_value = str(challenge.get("token") or "").strip()
    if not c_value:
        return None

    pow_payload = challenge.get("proofofwork") or {}
    if pow_payload.get("required") and pow_payload.get("seed"):
        p_value = await _solve_challenge_pow_async(
            resolved_user_agent,
            seed=str(pow_payload.get("seed") or ""),
            difficulty=str(pow_payload.get("difficulty") or DEFAULT_SENTINEL_DIFF) or DEFAULT_SENTINEL_DIFF,
        )
    else:
        p_value = await build_sentinel_pow_token_async(resolved_user_agent)

    return json.dumps(
        {
            "p": p_value,
            "t": "",
            "c": c_value,
            "id": resolved_device_id,
            "flow": flow,
        },
        separators=(",", ":"),
    )


def build_sentinel_token(
    session: Session,
    device_id: str,
    *,
    flow: str = "authorize_continue",
    user_agent: Optional[str] = None,
    sec_ch_ua: Optional[str] = None,
    impersonate: Optional[str] = None,
    extra_headers: Optional[dict[str, str]] = None,
) -> Optional[str]:
    return _run_async_from_sync(
        lambda: build_sentinel_token_async(
            session,
            device_id,
            flow=flow,
            user_agent=user_agent,
            sec_ch_ua=sec_ch_ua,
            impersonate=impersonate,
            extra_headers=extra_headers,
        )
    )


def _run_async_from_sync(factory):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())

    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def runner() -> None:
        try:
            result["value"] = asyncio.run(factory())
        except BaseException as exc:  # pragma: no cover
            error["exc"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()

    if "exc" in error:
        raise error["exc"]
    return result.get("value")
