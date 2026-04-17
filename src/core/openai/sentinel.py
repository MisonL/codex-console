"""Helpers for OpenAI Sentinel proof-of-work tokens."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Sequence

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]


DEFAULT_SENTINEL_DIFF = "0fffff"
DEFAULT_MAX_ITERATIONS = 500_000
DEFAULT_BROWSER_LOCALE = "en-US"
DEFAULT_BROWSER_LANGUAGES = (DEFAULT_BROWSER_LOCALE, "en")
DEFAULT_SENTINEL_TOKEN_PREFIX = "gAAAAAC"
_JSON_SEPARATORS = (",", ":")
_TIMEZONE_OVERRIDE_ENV = "OPENAI_SENTINEL_TIMEZONE"
_TIMEZONE_LABEL_OVERRIDE_ENV = "OPENAI_SENTINEL_TIMEZONE_LABEL"
_TOKEN_PREFIX_ENV = "OPENAI_SENTINEL_TOKEN_PREFIX"
_LOCALE_OVERRIDE_ENV = "OPENAI_SENTINEL_LOCALE"
_LANGUAGES_OVERRIDE_ENV = "OPENAI_SENTINEL_LANGUAGES"
_TIMEZONE_LABELS = {
    "America/Los_Angeles": ("Pacific Standard Time", "Pacific Daylight Time"), "America/New_York": ("Eastern Standard Time", "Eastern Daylight Time"),
    "Asia/Shanghai": "China Standard Time", "Europe/Berlin": ("Central European Standard Time", "Central European Summer Time"),
    "Europe/London": ("Greenwich Mean Time", "British Summer Time"),
}
_TIMEZONE_OFFSET_PATTERN = re.compile(r"^(?P<sign>[+-])(?P<hours>\d{2}):?(?P<minutes>\d{2})$")


@dataclass(frozen=True)
class BrowserFingerprintProfile:
    """Browser fingerprint hints used to build a less repetitive payload."""

    screen_signature: int
    js_heap_size_limit: int
    hardware_concurrency: int
    navigator_key: str
    document_key: str
    window_key: str


_BROWSER_PROFILE_POOLS = {
    "windows": (BrowserFingerprintProfile(4000, 4294705152, 8, "ontransitionend", "location", "window"), BrowserFingerprintProfile(4160, 8589934592, 12, "scheduler", "documentElement", "navigator"), BrowserFingerprintProfile(3120, 4294705152, 10, "location", "compatMode", "document")),
    "mac": (BrowserFingerprintProfile(3000, 4294705152, 8, "location", "documentElement", "window"), BrowserFingerprintProfile(3120, 8589934592, 10, "onprogress", "location", "screen")),
    "linux": (BrowserFingerprintProfile(3000, 2147483648, 4, "onprogress", "compatMode", "window"), BrowserFingerprintProfile(4000, 4294705152, 8, "scheduler", "documentElement", "navigator")),
    "generic": (BrowserFingerprintProfile(3000, 4294705152, 8, "location", "location", "window"), BrowserFingerprintProfile(4000, 2147483648, 4, "onprogress", "documentElement", "navigator")),
}


class SentinelPOWError(RuntimeError):
    """Raised when a Sentinel proof-of-work token cannot be solved."""


def _normalize_locale(value: str | None) -> str:
    if not value:
        return DEFAULT_BROWSER_LOCALE
    raw_value = value.split(".", 1)[0].strip().replace("_", "-")
    if not raw_value:
        return DEFAULT_BROWSER_LOCALE
    parts = [part for part in raw_value.split("-") if part]
    if not parts:
        return DEFAULT_BROWSER_LOCALE
    if len(parts) == 1:
        return parts[0].lower()
    normalized = [parts[0].lower(), parts[1].upper(), *parts[2:]]
    return "-".join(normalized)


def _dedupe_preserving_order(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)


def _resolve_browser_locale(locale: str | None = None) -> str:
    if locale:
        return _normalize_locale(locale)
    env_locale = os.getenv(_LOCALE_OVERRIDE_ENV)
    if env_locale:
        return _normalize_locale(env_locale)
    return _normalize_locale(os.getenv("LC_ALL") or os.getenv("LANG"))


def _resolve_browser_languages(locale: str, languages: Sequence[str] | None = None) -> tuple[str, ...]:
    if languages:
        return _dedupe_preserving_order(tuple(_normalize_locale(item) for item in languages))
    env_languages = os.getenv(_LANGUAGES_OVERRIDE_ENV, "")
    if env_languages.strip():
        parsed = tuple(_normalize_locale(item) for item in env_languages.split(","))
        return _dedupe_preserving_order(parsed)
    primary_language = locale.split("-", 1)[0]
    defaults = (locale, primary_language, *DEFAULT_BROWSER_LANGUAGES)
    return _dedupe_preserving_order(defaults)


def _classify_user_agent(user_agent: str) -> str:
    normalized = user_agent.lower()
    if "windows" in normalized:
        return "windows"
    if "macintosh" in normalized or "mac os x" in normalized:
        return "mac"
    if "linux" in normalized or "x11" in normalized:
        return "linux"
    return "generic"


def _choose_browser_profile(user_agent: str) -> BrowserFingerprintProfile:
    pool_name = _classify_user_agent(user_agent)
    pool = _BROWSER_PROFILE_POOLS.get(pool_name) or _BROWSER_PROFILE_POOLS["generic"]
    return secrets.choice(pool)


def _resolve_timezone_from_override(value: str):
    normalized = value.strip()
    if not normalized:
        return None, None
    if ZoneInfo is not None:
        try:
            zone = ZoneInfo(normalized)
            return zone, normalized
        except Exception:
            pass
    match = _TIMEZONE_OFFSET_PATTERN.fullmatch(normalized)
    if match:
        hours = int(match.group("hours"))
        minutes = int(match.group("minutes"))
        total_minutes = hours * 60 + minutes
        if match.group("sign") == "-":
            total_minutes *= -1
        return timezone(timedelta(minutes=total_minutes)), normalized
    return None, None


def _format_timezone_offset(offset: timedelta | None) -> str:
    if offset is None:
        offset = timedelta(0)
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    absolute_minutes = abs(total_minutes)
    hours, minutes = divmod(absolute_minutes, 60)
    return f"GMT{sign}{hours:02d}{minutes:02d}"


def _resolve_timezone_label(browser_now: datetime, zone_key: str | None) -> str:
    label_override = os.getenv(_TIMEZONE_LABEL_OVERRIDE_ENV, "").strip()
    if label_override:
        return label_override
    if zone_key:
        label = _TIMEZONE_LABELS.get(zone_key)
        if isinstance(label, tuple):
            return label[1] if bool(browser_now.dst()) else label[0]
        if isinstance(label, str):
            return label
    return browser_now.tzname() or (zone_key.replace("_", " ") if zone_key else "Local Time")


def _resolve_browser_datetime() -> tuple[datetime, str | None]:
    timezone_override = os.getenv(_TIMEZONE_OVERRIDE_ENV, "")
    if timezone_override:
        zone, zone_key = _resolve_timezone_from_override(timezone_override)
        if zone is not None:
            return datetime.now(zone), zone_key
    browser_now = datetime.now().astimezone()
    zone_key = getattr(browser_now.tzinfo, "key", None) or os.getenv("TZ")
    return browser_now, zone_key


def _format_browser_time() -> str:
    """Return a browser-style local time string with a dynamic offset."""
    browser_now, zone_key = _resolve_browser_datetime()
    offset_text = _format_timezone_offset(browser_now.utcoffset())
    timezone_label = _resolve_timezone_label(browser_now, zone_key)
    return f"{browser_now.strftime('%a %b %d %Y %H:%M:%S')} {offset_text} ({timezone_label})"


def build_sentinel_config(
    user_agent: str,
    *,
    locale: str | None = None,
    languages: Sequence[str] | None = None,
) -> list[object]:
    """Build a browser-like fingerprint payload for the Sentinel PoW solver."""
    browser_locale = _resolve_browser_locale(locale)
    browser_languages = _resolve_browser_languages(browser_locale, languages)
    profile = _choose_browser_profile(user_agent)
    perf_ms = round(time.perf_counter_ns() / 1_000_000, 3)
    epoch_ms = round((time.time_ns() / 1_000_000) - perf_ms, 3)
    return [
        profile.screen_signature,
        _format_browser_time(),
        profile.js_heap_size_limit,
        0,
        user_agent,
        "",
        "",
        browser_locale,
        ",".join(browser_languages),
        0,
        profile.navigator_key,
        profile.document_key,
        profile.window_key,
        perf_ms,
        str(uuid.uuid4()),
        "",
        profile.hardware_concurrency,
        epoch_ms,
    ]


def _build_pow_payload_segments(config: Sequence[object]) -> tuple[bytes, bytes, bytes]:
    prefix = (json.dumps(config[:3], separators=_JSON_SEPARATORS, ensure_ascii=False)[:-1] + ",").encode("utf-8")
    middle = ("," + json.dumps(config[4:9], separators=_JSON_SEPARATORS, ensure_ascii=False)[1:-1] + ",").encode("utf-8")
    suffix = ("," + json.dumps(config[10:], separators=_JSON_SEPARATORS, ensure_ascii=False)[1:]).encode("utf-8")
    return prefix, middle, suffix


def _encode_pow_payload(segments: tuple[bytes, bytes, bytes], nonce: int) -> bytes:
    prefix, middle, suffix = segments
    nonce_bytes = str(nonce).encode("ascii")
    half_nonce_bytes = str(nonce >> 1).encode("ascii")
    return base64.b64encode(prefix + nonce_bytes + middle + half_nonce_bytes + suffix)


def solve_sentinel_pow(
    seed: str,
    difficulty: str,
    config: Sequence[object],
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> str:
    """Solve the Sentinel PoW challenge and return the base64 payload."""
    seed_hasher = hashlib.sha3_512(seed.encode("utf-8"))
    target = bytes.fromhex(difficulty)
    prefix_length = len(target)
    segments = _build_pow_payload_segments(config)

    for nonce in range(max_iterations):
        encoded = _encode_pow_payload(segments, nonce)
        digest = seed_hasher.copy()
        digest.update(encoded)
        if digest.digest()[:prefix_length] <= target:
            return encoded.decode("ascii")

    raise SentinelPOWError(f"failed to solve sentinel pow after {max_iterations} attempts")


async def solve_sentinel_pow_async(
    seed: str,
    difficulty: str,
    config: Sequence[object],
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> str:
    """Run PoW solving in a worker thread to avoid blocking the event loop."""
    return await asyncio.to_thread(solve_sentinel_pow, seed=seed, difficulty=difficulty, config=config, max_iterations=max_iterations)


def _generate_pow_seed() -> str:
    return secrets.token_hex(16)


def _resolve_token_prefix() -> str:
    return os.getenv(_TOKEN_PREFIX_ENV, DEFAULT_SENTINEL_TOKEN_PREFIX).strip() or DEFAULT_SENTINEL_TOKEN_PREFIX


def build_sentinel_pow_token(
    user_agent: str,
    difficulty: str = DEFAULT_SENTINEL_DIFF,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> str:
    """Build the `p` token required by the Sentinel request endpoint."""
    config = build_sentinel_config(user_agent)
    seed = _generate_pow_seed()
    solution = solve_sentinel_pow(seed, difficulty, config, max_iterations=max_iterations)
    return f"{_resolve_token_prefix()}{solution}"


async def build_sentinel_pow_token_async(
    user_agent: str,
    difficulty: str = DEFAULT_SENTINEL_DIFF,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> str:
    """Build the `p` token in a worker thread for async servers such as FastAPI."""
    return await asyncio.to_thread(build_sentinel_pow_token, user_agent=user_agent, difficulty=difficulty, max_iterations=max_iterations)
