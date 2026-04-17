#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from curl_cffi import requests as curl_requests
from curl_cffi.requests import AsyncSession


DEFAULT_SOURCE_DIR = Path("/tmp/distilled_healthy_tokens")
DEFAULT_TARGET_DIR = Path("/tmp/verified_usable_tokens")
DEFAULT_PROGRESS_LOG = Path("/tmp/full_sweep_progress.log")
DEFAULT_PROXY_URL = None
DEFAULT_API_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-5.4"
DEFAULT_PROMPT = "Return exactly: ok"
DEFAULT_CONCURRENCY = 15
DEFAULT_TIMEOUT_SECONDS = 60.0
SUCCESS_HTTP_STATUS = 200
DELETE_HTTP_STATUSES = frozenset({401, 403})
TOKEN_FIELDS = (
    ("access_token",),
    ("token",),
    ("api_key",),
    ("bearer_token",),
    ("tokens", "access_token"),
    ("auth", "access_token"),
)
TEXT_TYPES = frozenset({"output_text", "text"})


@dataclass(frozen=True)
class SweepConfig:
    source_dir: Path = DEFAULT_SOURCE_DIR
    target_dir: Path = DEFAULT_TARGET_DIR
    progress_log: Path = DEFAULT_PROGRESS_LOG
    proxy_url: str = DEFAULT_PROXY_URL
    api_url: str = DEFAULT_API_URL
    model: str = DEFAULT_MODEL
    prompt: str = DEFAULT_PROMPT
    concurrency: int = DEFAULT_CONCURRENCY
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True)
class VerificationOutcome:
    usable: bool
    http_status: Optional[int]
    error_message: str = ""
    output_text: str = ""


class ProgressTracker:
    def __init__(self, total: int, log_path: Path):
        self._total = total
        self._log_path = log_path
        self._completed = 0
        self._success = 0
        self._failure = 0
        self._latest_error = "-"
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_path.write_text("", encoding="utf-8")
        await self._write_line()

    async def record(self, *, success: bool, error_message: str = "", count_as_failure: bool = True) -> None:
        async with self._lock:
            self._completed += 1
            if success:
                self._success += 1
            elif count_as_failure:
                self._failure += 1
            if error_message:
                self._latest_error = error_message
            await self._write_line()

    async def _write_line(self) -> None:
        timestamp = datetime.now().isoformat(timespec="seconds")
        line = (
            f"{timestamp} progress={self._completed}/{self._total} "
            f"success={self._success} failure={self._failure} "
            f"latest_error={self._latest_error}\n"
        )
        with self._log_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)


def extract_bearer_token(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for path in TOKEN_FIELDS:
        current: Any = payload
        for key in path:
            if not isinstance(current, dict):
                current = ""
                break
            current = current.get(key)
        token = str(current or "").strip()
        if token:
            return token.removeprefix("Bearer ").strip()
    return ""


def extract_output_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    direct_text = str(payload.get("output_text") or "").strip()
    if direct_text:
        return direct_text
    outputs = payload.get("output")
    if not isinstance(outputs, list):
        return ""
    chunks: list[str] = []
    for item in outputs:
        if not isinstance(item, dict):
            continue
        content_items = item.get("content")
        if not isinstance(content_items, list):
            continue
        for content in content_items:
            if not isinstance(content, dict):
                continue
            if str(content.get("type") or "").strip().lower() not in TEXT_TYPES:
                continue
            text = str(content.get("text") or "").strip()
            if text:
                chunks.append(text)
    return "\n".join(chunks).strip()


def build_request_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def build_request_body(config: SweepConfig) -> dict[str, Any]:
    return {
        "model": config.model,
        "input": config.prompt,
        "max_output_tokens": 16,
    }


def load_credential_payload(path: Path) -> dict[str, Any]:
    raw_text = path.read_text(encoding="utf-8")
    payload = json.loads(raw_text)
    if not isinstance(payload, dict):
        raise ValueError("凭证文件顶层必须是 JSON object")
    return payload


async def verify_credential(
    path: Path,
    session: AsyncSession,
    config: SweepConfig,
) -> VerificationOutcome:
    try:
        payload = load_credential_payload(path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return VerificationOutcome(False, None, f"{path.name}: 凭证读取失败: {exc}")

    token = extract_bearer_token(payload)
    if not token:
        return VerificationOutcome(False, None, f"{path.name}: 缺少 access token")

    try:
        response = await session.post(
            config.api_url,
            headers=build_request_headers(token),
            json=build_request_body(config),
            timeout=config.timeout_seconds,
        )
    except curl_requests.RequestsError as exc:
        return VerificationOutcome(False, None, f"{path.name}: 请求异常: {exc}")
    except Exception as exc:
        return VerificationOutcome(False, None, f"{path.name}: 未知请求异常: {exc}")

    if response.status_code != SUCCESS_HTTP_STATUS:
        reason = summarize_http_error(response)
        return VerificationOutcome(False, response.status_code, f"{path.name}: {reason}")

    try:
        response_json = response.json()
    except Exception as exc:
        return VerificationOutcome(False, response.status_code, f"{path.name}: 响应 JSON 无法解析: {exc}")

    output_text = extract_output_text(response_json)
    if not output_text:
        return VerificationOutcome(False, response.status_code, f"{path.name}: HTTP 200 但未成功出字")
    return VerificationOutcome(True, response.status_code, output_text=output_text)


def summarize_http_error(response: Any) -> str:
    status_code = getattr(response, "status_code", None)
    text = ""
    try:
        body = response.text
    except Exception:
        body = ""
    if body:
        compact_body = " ".join(str(body).split())
        text = compact_body[:240]
    if status_code in DELETE_HTTP_STATUSES:
        return f"HTTP {status_code}: {text or '认证失败'}"
    return f"HTTP {status_code or 'unknown'}: {text or '请求失败'}"


def archive_verified_file(source_path: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    source_path.replace(target_dir / source_path.name)


def delete_invalid_file(path: Path) -> None:
    if path.exists():
        path.unlink()


async def process_file(
    path: Path,
    session: AsyncSession,
    config: SweepConfig,
    progress: ProgressTracker,
    semaphore: asyncio.Semaphore,
) -> None:
    async with semaphore:
        outcome = await verify_credential(path, session, config)
        if outcome.usable:
            archive_verified_file(path, config.target_dir)
            await progress.record(success=True)
            return
        delete_invalid_file(path)
        await progress.record(success=False, error_message=outcome.error_message)


async def run_sweep(config: SweepConfig) -> int:
    files = sorted(config.source_dir.glob("*.json"))
    progress = ProgressTracker(len(files), config.progress_log)
    await progress.initialize()
    if not files:
        return 0

    config.target_dir.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(config.concurrency)
    async with AsyncSession(
        impersonate="chrome110",
        proxy=config.proxy_url,
        timeout=config.timeout_seconds,
        max_clients=config.concurrency,
    ) as session:
        await asyncio.gather(
            *(process_file(path, session, config, progress, semaphore) for path in files)
        )
    return len(files)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="全量核验 distilled healthy tokens 并清理失效凭证")
    parser.add_argument("--source-dir", default=str(DEFAULT_SOURCE_DIR), help="待核验凭证目录")
    parser.add_argument("--target-dir", default=str(DEFAULT_TARGET_DIR), help="有效凭证归档目录")
    parser.add_argument("--progress-log", default=str(DEFAULT_PROGRESS_LOG), help="进度日志路径")
    parser.add_argument("--proxy-url", default=DEFAULT_PROXY_URL, help="代理地址")
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help="Responses API 地址")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="核验模型")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="核验提示词")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY, help="最大并发数")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS, help="单请求超时秒数")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = SweepConfig(
        source_dir=Path(args.source_dir),
        target_dir=Path(args.target_dir),
        progress_log=Path(args.progress_log),
        proxy_url=str(args.proxy_url).strip(),
        api_url=str(args.api_url).strip(),
        model=str(args.model).strip(),
        prompt=str(args.prompt),
        concurrency=max(1, int(args.concurrency)),
        timeout_seconds=max(1.0, float(args.timeout)),
    )
    processed = asyncio.run(run_sweep(config))
    print(f"已处理 {processed} 个凭证文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
