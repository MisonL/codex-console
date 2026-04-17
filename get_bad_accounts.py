#!/usr/bin/env python3
from __future__ import annotations

import argparse
import codecs
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Iterator, TextIO
from urllib.request import Request, urlopen


DEFAULT_URL = "http://127.0.0.1:8317/v0/management/auth-files"
DEFAULT_TOKEN = "admin123456"
DEFAULT_OUTPUT = Path("/tmp/all_bad_accounts.txt")
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_CHUNK_SIZE = 64 * 1024
BAD_STATUSES = frozenset({"expired", "error"})
FILENAME_FIELDS = ("name", "filename", "file_name")


@dataclass(frozen=True)
class FetchConfig:
    url: str = DEFAULT_URL
    token: str = DEFAULT_TOKEN
    output_path: Path = DEFAULT_OUTPUT
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    chunk_size: int = DEFAULT_CHUNK_SIZE


class StreamingJsonArrayReader:
    def __init__(self, text_chunks: Iterator[str]):
        self._chunks = iter(text_chunks)
        self._buffer = ""
        self._cursor = 0
        self._decoder = json.JSONDecoder()
        self._eof = False

    def iter_items(self) -> Iterator[Any]:
        if not self._move_to_target_array():
            return
        while True:
            token = self._peek_non_whitespace()
            if token == "]":
                self._cursor += 1
                return
            yield self._decode_value()
            self._consume_array_separator()
            self._compact_buffer()

    def _move_to_target_array(self) -> bool:
        token = self._peek_non_whitespace()
        if token == "[":
            self._cursor += 1
            return True
        if token != "{":
            raise ValueError(f"不支持的 JSON 顶层结构: {token!r}")

        self._cursor += 1
        while True:
            token = self._peek_non_whitespace()
            if token == "}":
                self._cursor += 1
                return False

            key = self._decode_value()
            if not isinstance(key, str):
                raise ValueError("JSON 对象键必须为字符串")

            self._expect(":")
            if key == "files":
                if self._peek_non_whitespace() != "[":
                    self._decode_value()
                    return False
                self._cursor += 1
                return True

            self._decode_value()
            self._consume_object_separator()
            self._compact_buffer()

    def _decode_value(self) -> Any:
        self._skip_whitespace()
        while True:
            try:
                value, end = self._decoder.raw_decode(self._buffer, self._cursor)
                self._cursor = end
                return value
            except json.JSONDecodeError as exc:
                if not self._fill_buffer():
                    raise ValueError("JSON 内容不完整或格式无效") from exc

    def _consume_array_separator(self) -> None:
        token = self._peek_non_whitespace()
        if token == ",":
            self._cursor += 1
            return
        if token == "]":
            return
        raise ValueError(f"数组分隔符无效: {token!r}")

    def _consume_object_separator(self) -> None:
        token = self._peek_non_whitespace()
        if token == ",":
            self._cursor += 1
            return
        if token == "}":
            self._cursor += 1
            return
        raise ValueError(f"对象分隔符无效: {token!r}")

    def _expect(self, expected: str) -> None:
        token = self._peek_non_whitespace()
        if token != expected:
            raise ValueError(f"期待字符 {expected!r}，实际为 {token!r}")
        self._cursor += 1

    def _peek_non_whitespace(self) -> str:
        self._skip_whitespace()
        self._ensure_data()
        return self._buffer[self._cursor]

    def _skip_whitespace(self) -> None:
        while True:
            self._ensure_data()
            if self._cursor >= len(self._buffer):
                return
            if not self._buffer[self._cursor].isspace():
                return
            self._cursor += 1

    def _ensure_data(self) -> None:
        while self._cursor >= len(self._buffer) and self._fill_buffer():
            continue
        if self._cursor >= len(self._buffer):
            raise ValueError("JSON 数据提前结束")

    def _fill_buffer(self) -> bool:
        if self._eof:
            return False
        try:
            self._buffer += next(self._chunks)
            return True
        except StopIteration:
            self._eof = True
            return False

    def _compact_buffer(self) -> None:
        if self._cursor < DEFAULT_CHUNK_SIZE:
            return
        self._buffer = self._buffer[self._cursor :]
        self._cursor = 0


def _iter_text_chunks(stream: BinaryIO, chunk_size: int, encoding: str) -> Iterator[str]:
    decoder = codecs.getincrementaldecoder(encoding)()
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            tail = decoder.decode(b"", final=True)
            if tail:
                yield tail
            return
        text = decoder.decode(chunk)
        if text:
            yield text


def _pick_filename(item: dict[str, Any]) -> str:
    for field in FILENAME_FIELDS:
        value = str(item.get(field) or "").strip()
        if value:
            return value
    raise ValueError(f"命中坏账号条件但缺少文件名字段，可用键: {sorted(item.keys())}")


def _is_bad_item(item: dict[str, Any]) -> bool:
    status = str(item.get("status") or "").strip().lower()
    status_message = str(item.get("status_message") or "").strip().lower()
    return status in BAD_STATUSES or "reused" in status_message


def iter_bad_account_names(stream: BinaryIO, chunk_size: int, encoding: str) -> Iterator[str]:
    parser = StreamingJsonArrayReader(_iter_text_chunks(stream, chunk_size, encoding))
    for item in parser.iter_items():
        if not isinstance(item, dict):
            continue
        if _is_bad_item(item):
            yield _pick_filename(item)


def write_bad_accounts(stream: BinaryIO, output: TextIO, chunk_size: int, encoding: str) -> int:
    count = 0
    for name in iter_bad_account_names(stream, chunk_size, encoding):
        output.write(f"{name}\n")
        count += 1
    return count


def fetch_bad_accounts(config: FetchConfig) -> int:
    request = Request(
        config.url,
        headers={
            "Authorization": f"Bearer {config.token}",
            "Accept": "application/json",
        },
        method="GET",
    )
    config.output_path.parent.mkdir(parents=True, exist_ok=True)

    with urlopen(request, timeout=config.timeout_seconds) as response:
        status_code = getattr(response, "status", None)
        if status_code != 200:
            body = response.read(512).decode("utf-8", errors="replace")
            raise RuntimeError(f"请求失败，HTTP {status_code}: {body}")

        encoding = response.headers.get_content_charset() or "utf-8"
        with config.output_path.open("w", encoding="utf-8", newline="\n") as output:
            return write_bad_accounts(response, output, config.chunk_size, encoding)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="提取 auth-files 中待清理的坏账号文件名")
    parser.add_argument("--url", default=DEFAULT_URL, help="auth-files 接口地址")
    parser.add_argument("--token", default=DEFAULT_TOKEN, help="Bearer Token")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="输出文件路径，默认 /tmp/all_bad_accounts.txt",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS, help="HTTP 超时秒数")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE, help="流式读取块大小")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    config = FetchConfig(
        url=args.url,
        token=args.token,
        output_path=Path(args.output),
        timeout_seconds=args.timeout,
        chunk_size=args.chunk_size,
    )
    count = fetch_bad_accounts(config)
    print(f"已写入 {count} 个待清理文件到 {config.output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
