"""
代理 URL 归一化与错误诊断工具。
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import quote, unquote, urlsplit


_SUPPORTED_PROXY_SCHEMES = {"http", "https", "socks5", "socks5h"}
_TRANSIENT_PROXY_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


def _normalize_scheme(scheme: Optional[str]) -> str:
    value = str(scheme or "http").strip().lower()
    return value if value in _SUPPORTED_PROXY_SCHEMES else "http"


def _unwrap_nested_proxy_url(raw_value: str) -> str:
    parsed = urlsplit(raw_value)
    if parsed.scheme and parsed.netloc.endswith(":") and parsed.path.startswith("//"):
        nested = f"{parsed.netloc[:-1]}:{parsed.path}"
        if nested != raw_value:
            return nested
    return raw_value


def split_proxy_components(
    proxy_type: Optional[str],
    host: Optional[str],
    port: Optional[int],
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> Dict[str, Any]:
    scheme = _normalize_scheme(proxy_type)
    raw_host = str(host or "").strip()
    if not raw_host:
        raise ValueError("代理主机不能为空")

    parsed_username = ""
    parsed_password = ""
    parsed_host = raw_host
    parsed_port = int(port or 0)

    candidate = raw_host
    if "://" in candidate:
        candidate = _unwrap_nested_proxy_url(candidate)
        parsed = urlsplit(candidate)
        if not parsed.hostname:
            raise ValueError("代理地址格式无效")
        scheme = _normalize_scheme(parsed.scheme or scheme)
        parsed_host = str(parsed.hostname or "").strip()
        parsed_port = int(parsed.port or parsed_port or 0)
        parsed_username = unquote(parsed.username or "").strip()
        parsed_password = unquote(parsed.password or "").strip()
    else:
        parsed_host = raw_host.strip().rstrip("/")

    resolved_username = str(username or "").strip() or parsed_username
    resolved_password = str(password or "").strip() or parsed_password

    if not parsed_host:
        raise ValueError("代理主机不能为空")
    if parsed_port <= 0:
        raise ValueError("代理端口必须大于 0")

    return {
        "type": scheme,
        "host": parsed_host,
        "port": parsed_port,
        "username": resolved_username or None,
        "password": resolved_password or None,
    }


def build_proxy_url_from_components(
    proxy_type: Optional[str],
    host: Optional[str],
    port: Optional[int],
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> Optional[str]:
    try:
        parts = split_proxy_components(proxy_type, host, port, username, password)
    except ValueError:
        return None

    auth = ""
    if parts["username"] and parts["password"]:
        safe_user = quote(str(parts["username"]), safe="")
        safe_password = quote(str(parts["password"]), safe="")
        auth = f"{safe_user}:{safe_password}@"

    return f"{parts['type']}://{auth}{parts['host']}:{parts['port']}"


def normalize_proxy_url(proxy_url: Optional[str], default_scheme: str = "http") -> Optional[str]:
    raw_value = str(proxy_url or "").strip()
    if not raw_value:
        return None

    candidate = _unwrap_nested_proxy_url(raw_value)
    if "://" not in candidate:
        candidate = f"{_normalize_scheme(default_scheme)}://{candidate}"

    parsed = urlsplit(candidate)
    if not parsed.hostname:
        return None

    scheme = _normalize_scheme(parsed.scheme or default_scheme)
    port = parsed.port
    if port is None:
        if scheme in {"http", "https"}:
            port = 80
        elif scheme in {"socks5", "socks5h"}:
            port = 1080

    return build_proxy_url_from_components(
        scheme,
        parsed.hostname,
        port,
        unquote(parsed.username or "") or None,
        unquote(parsed.password or "") or None,
    )


def build_requests_proxy_map(proxy_url: Optional[str]) -> Optional[Dict[str, str]]:
    normalized = normalize_proxy_url(proxy_url)
    if not normalized:
        return None
    return {"http": normalized, "https": normalized}


def is_retryable_proxy_probe(status_code: Optional[int], error_message: Optional[str]) -> bool:
    if status_code in _TRANSIENT_PROXY_STATUS_CODES:
        return True

    text = str(error_message or "").strip().lower()
    retry_markers = (
        "timed out",
        "timeout",
        "temporarily unavailable",
        "connection reset",
        "connection aborted",
        "connection refused",
        "tlsv1 alert internal error",
        "recv failure",
        "network is unreachable",
        "could not resolve host",
    )
    return any(marker in text for marker in retry_markers)


def diagnose_proxy_failure(
    *,
    status_code: Optional[int] = None,
    error_message: Optional[str] = None,
    target_url: Optional[str] = None,
) -> Dict[str, str]:
    text = str(error_message or "").strip()
    lower_text = text.lower()
    target = str(target_url or "").strip()

    if status_code == 407 or "407" in lower_text or "proxy authentication required" in lower_text:
        return {
            "code": "proxy_auth_failed",
            "message": "代理认证失败，请检查用户名密码；若账号欠费、流量耗尽或当前出口 IP 未加白，也可能触发此错误。",
        }
    if status_code == 506 or " 506" in lower_text or lower_text.startswith("506") or "http 506" in lower_text:
        return {
            "code": "proxy_account_blocked",
            "message": "代理服务拒绝当前请求，常见原因是账号欠费、流量用尽或供应商要求先加白当前出口 IP。",
        }
    if "proxy connect aborted" in lower_text or "proxy connect" in lower_text:
        return {
            "code": "proxy_connect_aborted",
            "message": "代理连接在 CONNECT 阶段被中止，请优先检查代理地址格式、认证信息和供应商侧策略。",
        }
    if status_code in {401, 403}:
        return {
            "code": "target_forbidden",
            "message": "目标站点拒绝了当前请求，可能与出口 IP 风控、地区限制或上游策略有关。",
        }
    if status_code and status_code >= 400:
        return {
            "code": "http_error",
            "message": f"代理请求返回 HTTP {status_code}。",
        }
    if target and "auth.openai.com" in target:
        return {
            "code": "target_url_rejected",
            "message": "代理访问 OpenAI 认证站点失败，可能是该目标域名被代理商策略拦截。",
        }
    return {
        "code": "proxy_error",
        "message": text or "代理连接失败",
    }
