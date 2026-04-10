"""
设置 API 路由
"""

import logging
import os
import time
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from ...config.settings import get_settings, update_settings
from ...config.constants import RegistrationWaitStrategy, normalize_registration_wait_strategy
from ...core.auto_registration import (
    trigger_auto_registration_check,
    update_auto_registration_state,
)
from ...core.proxy_utils import (
    build_requests_proxy_map,
    diagnose_proxy_failure,
    is_retryable_proxy_probe,
    normalize_proxy_url,
    split_proxy_components,
)
from ...database import crud
from ...database.session import get_db
from ...services import EmailServiceType

logger = logging.getLogger(__name__)
router = APIRouter()

_PROXY_EXIT_IP_TEST_URL = "https://api.ipify.org?format=json"
_PROXY_EXIT_IP_FALLBACK_URLS = (
    _PROXY_EXIT_IP_TEST_URL,
    "https://api64.ipify.org?format=json",
    "https://ifconfig.me/ip",
)
_PROXY_OPENAI_TEST_URL = "https://auth.openai.com"
_PROXY_TEST_TIMEOUT_SECONDS = 8
_PROXY_TEST_MAX_ATTEMPTS = 2
_PROXY_TEST_RETRY_DELAY_SECONDS = 0.35
_PROXY_SERVER_PUBLIC_IP_CACHE_TTL_SECONDS = 60
_PROXY_CURL_CFFI_INCOMPATIBLE_MESSAGE = "curl_cffi 代理探测失败，但标准 requests 验证成功，疑似 curl_cffi TLS 仿真与代理网关不兼容。"
_NON_FATAL_OPENAI_DIAGNOSIS_CODES = {"target_forbidden"}
_PROXY_LEAK_WARNING_MESSAGE = "警告：代理未生效，当前检测到的是服务器真实 IP，请检查代理配置格式。"
_PROXY_SERVER_IP_UNAVAILABLE_MESSAGE = "无法确认服务器公网 IP，已拒绝将当前代理判定为成功，请先检查服务器直连网络。"
_PROXY_EXIT_IP_MISSING_MESSAGE = "代理探测未返回可识别的出口 IP，已拒绝将当前代理判定为成功。"

_server_public_ip_cache: Dict[str, Any] = {
    "value": "",
    "expires_at": 0.0,
}


# ============== Pydantic Models ==============

class SettingItem(BaseModel):
    """设置项"""
    key: str
    value: str
    description: Optional[str] = None
    category: str = "general"


class SettingUpdateRequest(BaseModel):
    """设置更新请求"""
    value: str


class ProxySettings(BaseModel):
    """代理设置"""
    enabled: bool = False
    type: str = "http"  # http, socks5
    host: str = "127.0.0.1"
    port: int = 7890
    username: Optional[str] = None
    password: Optional[str] = None


class RegistrationSettings(BaseModel):
    """注册设置"""
    max_retries: int = 3
    timeout: int = 120
    default_password_length: int = 12
    sleep_min: int = 5
    sleep_max: int = 30
    wait_strategy: str = RegistrationWaitStrategy.START.value
    entry_flow: str = "native"
    auto_enabled: bool = False
    auto_check_interval: int = 60
    auto_min_ready_auth_files: int = 1
    auto_email_service_type: str = "tempmail"
    auto_email_service_id: int = 0
    auto_proxy: Optional[str] = None
    auto_interval_min: int = 5
    auto_interval_max: int = 30
    auto_concurrency: int = 1
    auto_mode: str = "pipeline"
    auto_cpa_service_id: int = 0


class WebUISettings(BaseModel):
    """Web UI 设置"""
    host: Optional[str] = None
    port: Optional[int] = None
    debug: Optional[bool] = None
    access_password: Optional[str] = None


class AllSettings(BaseModel):
    """所有设置"""
    proxy: ProxySettings
    registration: RegistrationSettings
    webui: WebUISettings


class AutoQuickRefreshSettingsRequest(BaseModel):
    enabled: bool = False
    interval_minutes: int = 30
    retry_limit: int = 2
    run_now: bool = False


def _normalize_proxy_payload(
    proxy_type: Optional[str],
    host: Optional[str],
    port: Optional[int],
    username: Optional[str],
    password: Optional[str],
) -> Dict[str, Any]:
    try:
        return split_proxy_components(proxy_type, host, port, username, password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _request_via_curl_cffi(target_url: str, proxies: Optional[Dict[str, str]]):
    from curl_cffi import requests as cffi_requests

    proxy_url = ""
    if isinstance(proxies, dict):
        proxy_url = str(proxies.get("https") or proxies.get("http") or "").strip()

    request_kwargs: Dict[str, Any] = {
        "timeout": _PROXY_TEST_TIMEOUT_SECONDS,
        "impersonate": "chrome120",
        "allow_redirects": False,
    }
    if proxy_url:
        # curl_cffi 在带认证代理上优先使用单值 proxy 参数，避免字典映射被底层忽略。
        request_kwargs["proxy"] = proxy_url
    elif proxies:
        request_kwargs["proxies"] = proxies

    return cffi_requests.get(
        target_url,
        **request_kwargs,
    )


def _request_via_requests(target_url: str, proxies: Optional[Dict[str, str]]):
    import requests

    with requests.Session() as session:
        # 禁用环境变量代理，避免 NO_PROXY / HTTP_PROXY 覆盖显式配置导致误判为直连。
        session.trust_env = False
        return session.get(
            target_url,
            proxies=proxies,
            timeout=_PROXY_TEST_TIMEOUT_SECONDS,
            allow_redirects=False,
        )


def _probe_proxy_with_transport(
    proxy_url: str,
    target_url: str,
    label: str,
    transport_name: str,
    request_func: Callable[[str, Optional[Dict[str, str]]], Any],
    *,
    retryable: bool = True,
) -> Dict[str, Any]:
    proxies = build_requests_proxy_map(proxy_url)
    if not proxies:
        return {
            "name": label,
            "url": target_url,
            "transport": transport_name,
            "success": False,
            "diagnosis_code": "invalid_proxy_url",
            "message": "代理地址格式无效，请检查协议、主机、端口和认证信息。",
        }
    last_result: Dict[str, Any] = {}
    max_attempts = _PROXY_TEST_MAX_ATTEMPTS if retryable else 1

    for attempt in range(1, max_attempts + 1):
        started = time.time()
        try:
            response = request_func(target_url, proxies)
            elapsed_ms = round((time.time() - started) * 1000)
            if response.status_code < 400:
                return {
                    "name": label,
                    "url": target_url,
                    "transport": transport_name,
                    "success": True,
                    "status_code": response.status_code,
                    "elapsed_ms": elapsed_ms,
                    "response": response,
                }

            diagnosis = diagnose_proxy_failure(
                status_code=response.status_code,
                target_url=target_url,
            )
            last_result = {
                "name": label,
                "url": target_url,
                "transport": transport_name,
                "success": False,
                "status_code": response.status_code,
                "elapsed_ms": elapsed_ms,
                "diagnosis_code": diagnosis["code"],
                "message": diagnosis["message"],
            }
            if attempt < max_attempts and is_retryable_proxy_probe(response.status_code, None):
                time.sleep(_PROXY_TEST_RETRY_DELAY_SECONDS * attempt)
                continue
            return last_result
        except Exception as exc:
            elapsed_ms = round((time.time() - started) * 1000)
            diagnosis = diagnose_proxy_failure(
                error_message=str(exc),
                target_url=target_url,
            )
            last_result = {
                "name": label,
                "url": target_url,
                "transport": transport_name,
                "success": False,
                "elapsed_ms": elapsed_ms,
                "diagnosis_code": diagnosis["code"],
                "message": diagnosis["message"],
                "error": str(exc),
            }
            if attempt < max_attempts and is_retryable_proxy_probe(None, str(exc)):
                time.sleep(_PROXY_TEST_RETRY_DELAY_SECONDS * attempt)
                continue
            return last_result

    return last_result


def _is_proxy_connect_aborted_error(*probe_results: Dict[str, Any]) -> bool:
    markers = ("curl: (56)", "(56)", "proxy connect aborted")
    for probe in probe_results:
        text = " ".join(
            str(part or "")
            for part in (
                probe.get("error"),
                probe.get("message"),
                probe.get("diagnosis_code"),
            )
        ).lower()
        if any(marker in text for marker in markers):
            return True
    return False


def _resolve_server_public_ip() -> str:
    try:
        import requests
    except Exception:
        return ""

    now = time.time()
    cached_ip = str(_server_public_ip_cache.get("value") or "").strip()
    cache_expires_at = float(_server_public_ip_cache.get("expires_at") or 0.0)
    if cached_ip and cache_expires_at > now:
        return cached_ip

    try:
        with requests.Session() as session:
            # 必须直连获取服务器公网 IP，避免被环境代理污染泄漏判定。
            session.trust_env = False
            for target_url in _PROXY_EXIT_IP_FALLBACK_URLS:
                try:
                    response = session.get(
                        target_url,
                        timeout=_PROXY_TEST_TIMEOUT_SECONDS,
                        allow_redirects=False,
                    )
                except Exception:
                    continue
                if response.status_code >= 400:
                    continue
                ip = _extract_ip_from_response(response)
                if ip:
                    _server_public_ip_cache["value"] = ip
                    _server_public_ip_cache["expires_at"] = now + _PROXY_SERVER_PUBLIC_IP_CACHE_TTL_SECONDS
                    return ip
    except Exception:
        return ""
    return ""


def _append_webshare_ip_hint(message: str) -> str:
    server_ip = _resolve_server_public_ip()
    if server_ip:
        hint = f"请检查 Webshare 后台是否已授权当前服务器 IP ({server_ip})"
    else:
        hint = "请检查 Webshare 后台是否已授权当前服务器 IP"
    if hint in message:
        return message
    if not message:
        return hint
    return f"{message}；{hint}"


def _safe_normalize_proxy_url(proxy_url: str) -> str:
    try:
        normalized = normalize_proxy_url(proxy_url)
    except Exception:
        normalized = None
    return normalized or str(proxy_url or "")


def _classify_proxy_exception(error_text: str, target_url: str = "") -> Dict[str, str]:
    diagnosis = diagnose_proxy_failure(
        error_message=error_text,
        target_url=target_url,
    )
    diagnosis_code = str(diagnosis.get("code") or "proxy_error")
    message = str(diagnosis.get("message") or "代理连接失败")

    if _is_proxy_connect_aborted_error(
        {"error": error_text, "message": message, "diagnosis_code": diagnosis_code}
    ):
        diagnosis_code = "proxy_connect_aborted"
        message = _append_webshare_ip_hint(message)

    return {
        "diagnosis_code": diagnosis_code,
        "message": message,
    }


def _sanitize_proxy_payload(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: Dict[str, Any] = {}
        for key, item in value.items():
            if key == "response":
                continue
            cleaned[key] = _sanitize_proxy_payload(item)
        return cleaned
    if isinstance(value, list):
        return [_sanitize_proxy_payload(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _json_safe_proxy_payload(value: Any) -> Any:
    sanitized = _sanitize_proxy_payload(value)
    try:
        return jsonable_encoder(sanitized)
    except Exception:
        return sanitized


def _extract_ip_from_response(response: Any) -> str:
    if response is None:
        return ""

    payload: Dict[str, Any] = {}
    try:
        parsed = response.json() if hasattr(response, "json") else {}
        if isinstance(parsed, dict):
            payload = parsed
    except Exception:
        payload = {}

    for key in ("ip", "query", "origin"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value.split(",")[0].strip()

    text = str(getattr(response, "text", "") or "").strip()
    if not text:
        return ""
    return text.split(",")[0].strip()


def _safe_isoformat_datetime(value: Any) -> Optional[str]:
    if value is None:
        return None
    iso_fn = getattr(value, "isoformat", None)
    if callable(iso_fn):
        try:
            return str(iso_fn())
        except Exception:
            pass
    text = str(value).strip()
    return text or None


def _safe_parse_proxy_port(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(str(value).strip())
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _safe_parse_int(value: Any, default: int = 0) -> int:
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _safe_proxy_to_dict(proxy: Any, include_password: bool = False) -> Dict[str, Any]:
    try:
        payload = proxy.to_dict(include_password=include_password)
        if isinstance(payload, dict):
            return payload
    except Exception:
        logger.exception(
            "Proxy serialization failed, fallback to safe serializer",
            extra={"proxy_id": getattr(proxy, "id", None)},
        )

    raw_type = getattr(proxy, "type", "http")
    raw_host = getattr(proxy, "host", "")
    raw_port = getattr(proxy, "port", None)
    raw_username = getattr(proxy, "username", None)
    raw_password = getattr(proxy, "password", None)

    try:
        normalized = split_proxy_components(raw_type, raw_host, raw_port, raw_username, raw_password)
    except Exception:
        normalized = {
            "type": str(raw_type or "http").strip() or "http",
            "host": str(raw_host or "").strip(),
            "port": _safe_parse_proxy_port(raw_port),
            "username": str(raw_username or "").strip() or None,
            "password": str(raw_password or "").strip() or None,
        }

    result: Dict[str, Any] = {
        "id": getattr(proxy, "id", None),
        "name": str(getattr(proxy, "name", "") or ""),
        "type": str(normalized.get("type") or "http"),
        "host": str(normalized.get("host") or ""),
        "port": normalized.get("port"),
        "username": normalized.get("username"),
        "enabled": bool(getattr(proxy, "enabled", False)),
        "is_default": bool(getattr(proxy, "is_default", False)),
        "priority": _safe_parse_int(getattr(proxy, "priority", 0), 0),
        "last_used": _safe_isoformat_datetime(getattr(proxy, "last_used", None)),
        "created_at": _safe_isoformat_datetime(getattr(proxy, "created_at", None)),
        "updated_at": _safe_isoformat_datetime(getattr(proxy, "updated_at", None)),
    }
    if include_password:
        result["password"] = normalized.get("password")
    else:
        result["has_password"] = bool(normalized.get("password"))
    return _json_safe_proxy_payload(result)


def _finalize_proxy_diagnostics_result(proxy_url: str, result: Any) -> Dict[str, Any]:
    normalized_proxy_url = _safe_normalize_proxy_url(proxy_url)
    if not isinstance(result, dict):
        return {
            "success": False,
            "proxy_url": normalized_proxy_url,
            "message": "代理诊断返回了非预期结果，请查看后端日志。",
            "diagnosis_code": "proxy_diagnostics_invalid_payload",
            "probes": [],
        }

    payload: Dict[str, Any] = dict(result)
    payload.pop("response", None)

    success = bool(payload.get("success"))
    payload["success"] = success
    payload["proxy_url"] = str(payload.get("proxy_url") or normalized_proxy_url)

    message = str(payload.get("message") or ("代理连接成功" if success else "代理连接失败")).strip()
    diagnosis_code = str(payload.get("diagnosis_code") or ("proxy_ok" if success else "proxy_error")).strip()
    error_text = str(payload.get("error") or "").strip()

    if (not success) and _is_proxy_connect_aborted_error(
        {"error": error_text, "message": message, "diagnosis_code": diagnosis_code}
    ):
        diagnosis_code = "proxy_connect_aborted"
        message = _append_webshare_ip_hint(
            "代理连接在 CONNECT 阶段被中止，请优先检查代理地址格式、认证信息和供应商侧策略。"
        )

    payload["message"] = message
    payload["diagnosis_code"] = diagnosis_code

    probes = payload.get("probes")
    payload["probes"] = probes if isinstance(probes, list) else []

    response_time = payload.get("response_time")
    if success:
        ip = str(payload.get("ip") or "").strip() or "unknown"
        if not message:
            message = f"代理连接成功，出口 IP: {ip}"
        if response_time is not None and "响应时间" not in message:
            message = f"{message}，响应时间: {response_time}ms"
        payload["message"] = message

    if error_text:
        payload["error"] = error_text
    else:
        payload.pop("error", None)

    return _json_safe_proxy_payload(payload)


def _build_proxy_route_error_result(proxy_url: str, exc: Exception, fallback_code: str) -> Dict[str, Any]:
    error_text = str(exc) or repr(exc)
    classified = _classify_proxy_exception(error_text)
    diagnosis_code = classified["diagnosis_code"]
    message = classified["message"]
    if diagnosis_code == "proxy_error":
        diagnosis_code = fallback_code
        message = "代理测试失败，请检查后端日志。"
    return _finalize_proxy_diagnostics_result(
        proxy_url,
        {
            "success": False,
            "proxy_url": _safe_normalize_proxy_url(proxy_url),
            "message": message,
            "diagnosis_code": diagnosis_code,
            "error": error_text,
            "probes": [],
        },
    )


def _safe_run_proxy_diagnostics(proxy_url: str) -> Dict[str, Any]:
    try:
        result = _run_proxy_diagnostics(proxy_url)
    except Exception as exc:
        logger.exception("Proxy diagnostics crashed unexpectedly", extra={"proxy_url": proxy_url})
        return _build_proxy_route_error_result(proxy_url, exc, "proxy_diagnostics_unhandled_exception")
    return _finalize_proxy_diagnostics_result(proxy_url, result)


def _probe_proxy_endpoint(proxy_url: str, target_url: str, label: str) -> Dict[str, Any]:
    try:
        cffi_probe = _probe_proxy_with_transport(
            proxy_url,
            target_url,
            label,
            "curl_cffi",
            _request_via_curl_cffi,
            retryable=True,
        )
        if cffi_probe.get("success"):
            return cffi_probe

        requests_probe = _probe_proxy_with_transport(
            proxy_url,
            target_url,
            label,
            "requests",
            _request_via_requests,
            retryable=False,
        )
        cffi_probe_view = dict(cffi_probe)
        cffi_probe_view.pop("response", None)

        if requests_probe.get("success"):
            merged = dict(requests_probe)
            merged["diagnosis_code"] = "curl_cffi_tls_incompatible"
            merged["message"] = _PROXY_CURL_CFFI_INCOMPATIBLE_MESSAGE
            merged["fallback_probe"] = cffi_probe_view
            return merged

        message = requests_probe.get("message") or cffi_probe.get("message") or "代理连接失败"
        diagnosis_code = requests_probe.get("diagnosis_code") or cffi_probe.get("diagnosis_code") or "proxy_error"
        if _is_proxy_connect_aborted_error(cffi_probe, requests_probe):
            message = _append_webshare_ip_hint(message)

        return {
            "name": label,
            "url": target_url,
            "transport": requests_probe.get("transport") or "requests",
            "success": False,
            "status_code": requests_probe.get("status_code") or cffi_probe.get("status_code"),
            "elapsed_ms": requests_probe.get("elapsed_ms") or cffi_probe.get("elapsed_ms"),
            "diagnosis_code": diagnosis_code,
            "message": message,
            "error": requests_probe.get("error") or cffi_probe.get("error"),
            "fallback_probe": cffi_probe_view,
        }
    except Exception as exc:
        logger.exception(
            "Proxy endpoint probe failed unexpectedly",
            extra={
                "proxy_url": proxy_url,
                "target_url": target_url,
                "probe_label": label,
            },
        )
        error_text = str(exc) or repr(exc)
        classified = _classify_proxy_exception(error_text, target_url)
        diagnosis_code = classified["diagnosis_code"]
        if diagnosis_code == "proxy_error":
            diagnosis_code = "proxy_probe_exception"
        return {
            "name": label,
            "url": target_url,
            "transport": "unknown",
            "success": False,
            "diagnosis_code": diagnosis_code,
            "message": classified["message"],
            "error": error_text,
        }


def _append_probe_result(probes: list[Dict[str, Any]], probe_result: Dict[str, Any]) -> None:
    fallback_probe = probe_result.get("fallback_probe")
    if isinstance(fallback_probe, dict):
        fallback_view = dict(fallback_probe)
        fallback_view.pop("response", None)
        probes.append(fallback_view)
    probe_view = dict(probe_result)
    probe_view.pop("response", None)
    probe_view.pop("fallback_probe", None)
    probes.append(probe_view)


def _run_proxy_diagnostics(proxy_url: str) -> Dict[str, Any]:
    probes: list[Dict[str, Any]] = []
    normalized_proxy_url = ""
    try:
        normalized_proxy_url = normalize_proxy_url(proxy_url)
        if not normalized_proxy_url:
            return {
                "success": False,
                "message": "代理地址格式无效，请检查协议、主机、端口和认证信息。",
                "diagnosis_code": "invalid_proxy_url",
            }

        exit_probe = _probe_proxy_endpoint(normalized_proxy_url, _PROXY_EXIT_IP_TEST_URL, "exit_ip")
        auth_probe: Optional[Dict[str, Any]] = None
        _append_probe_result(probes, exit_probe)

        if not exit_probe.get("success"):
            return {
                "success": False,
                "proxy_url": normalized_proxy_url,
                "message": exit_probe.get("message") or "代理连接失败",
                "diagnosis_code": exit_probe.get("diagnosis_code") or "proxy_error",
                "response_time": exit_probe.get("elapsed_ms"),
                "probes": probes,
            }

        ip = ""
        response = exit_probe.get("response")
        if response is not None:
            ip = _extract_ip_from_response(response)

        if not ip:
            return {
                "success": False,
                "proxy_url": normalized_proxy_url,
                "response_time": exit_probe.get("elapsed_ms"),
                "message": _PROXY_EXIT_IP_MISSING_MESSAGE,
                "diagnosis_code": "proxy_exit_ip_missing",
                "probes": probes,
            }

        server_public_ip = _resolve_server_public_ip()
        if not server_public_ip:
            return {
                "success": False,
                "proxy_url": normalized_proxy_url,
                "ip": ip,
                "response_time": exit_probe.get("elapsed_ms"),
                "message": _PROXY_SERVER_IP_UNAVAILABLE_MESSAGE,
                "diagnosis_code": "server_public_ip_unavailable",
                "probes": probes,
            }
        if ip and server_public_ip and ip == server_public_ip:
            return {
                "success": False,
                "proxy_url": normalized_proxy_url,
                "ip": ip,
                "server_ip": server_public_ip,
                "response_time": exit_probe.get("elapsed_ms"),
                "message": _PROXY_LEAK_WARNING_MESSAGE,
                "diagnosis_code": "proxy_leak_detected",
                "probes": probes,
            }

        auth_probe = _probe_proxy_endpoint(normalized_proxy_url, _PROXY_OPENAI_TEST_URL, "openai_auth")
        _append_probe_result(probes, auth_probe)

        if auth_probe.get("success"):
            message = f"代理连接成功，出口 IP: {ip or 'unknown'}"
            return {
                "success": True,
                "proxy_url": normalized_proxy_url,
                "ip": ip,
                "response_time": exit_probe.get("elapsed_ms"),
                "message": message,
                "probes": probes,
            }

        failure_message = auth_probe.get("message") or "代理访问 OpenAI 认证站点失败"
        if ip:
            failure_message = f"代理出口可用，出口 IP: {ip}；但访问 OpenAI 认证站点失败。{failure_message}"
            auth_diagnosis_code = str(auth_probe.get("diagnosis_code") or "")
            if auth_diagnosis_code in _NON_FATAL_OPENAI_DIAGNOSIS_CODES:
                return {
                    "success": True,
                    "proxy_url": normalized_proxy_url,
                    "ip": ip,
                    "response_time": exit_probe.get("elapsed_ms"),
                    "message": failure_message,
                    "diagnosis_code": "proxy_exit_ip_only",
                    "warning_diagnosis_code": auth_diagnosis_code,
                    "probes": probes,
                }
        return {
            "success": False,
            "proxy_url": normalized_proxy_url,
            "ip": ip,
            "response_time": auth_probe.get("elapsed_ms") or exit_probe.get("elapsed_ms"),
            "message": failure_message,
            "diagnosis_code": auth_probe.get("diagnosis_code") or "target_url_rejected",
            "probes": probes,
        }
    except Exception as exc:
        logger.exception("Proxy diagnostics failed", extra={"proxy_url": proxy_url})
        error_text = str(exc) or repr(exc)
        classified = _classify_proxy_exception(error_text)
        diagnosis_code = classified["diagnosis_code"]
        if diagnosis_code == "proxy_error":
            diagnosis_code = "proxy_diagnostics_exception"
            message = "代理诊断执行失败，请检查后端日志。"
        else:
            message = classified["message"]
        return {
            "success": False,
            "proxy_url": normalized_proxy_url or _safe_normalize_proxy_url(proxy_url),
            "message": message,
            "diagnosis_code": diagnosis_code,
            "error": error_text,
            "probes": probes,
        }


# ============== API Endpoints ==============

@router.get("")
def get_all_settings():
    """获取所有设置"""
    settings = get_settings()

    entry_flow_raw = str(settings.registration_entry_flow or "native").strip().lower()
    entry_flow = "abcard" if entry_flow_raw == "abcard" else "native"
    wait_strategy = normalize_registration_wait_strategy(getattr(settings, "registration_wait_strategy", "start"))

    return {
        "proxy": {
            "enabled": settings.proxy_enabled,
            "type": settings.proxy_type,
            "host": settings.proxy_host,
            "port": settings.proxy_port,
            "username": settings.proxy_username,
            "has_password": bool(settings.proxy_password),
            "dynamic_enabled": settings.proxy_dynamic_enabled,
            "dynamic_api_url": settings.proxy_dynamic_api_url,
            "dynamic_api_key_header": settings.proxy_dynamic_api_key_header,
            "dynamic_result_field": settings.proxy_dynamic_result_field,
            "has_dynamic_api_key": bool(settings.proxy_dynamic_api_key and settings.proxy_dynamic_api_key.get_secret_value()),
        },
        "registration": {
            "max_retries": settings.registration_max_retries,
            "timeout": settings.registration_timeout,
            "default_password_length": settings.registration_default_password_length,
            "sleep_min": settings.registration_sleep_min,
            "sleep_max": settings.registration_sleep_max,
            "wait_strategy": wait_strategy,
            "entry_flow": entry_flow,
            "auto_enabled": settings.registration_auto_enabled,
            "auto_check_interval": settings.registration_auto_check_interval,
            "auto_min_ready_auth_files": settings.registration_auto_min_ready_auth_files,
            "auto_email_service_type": settings.registration_auto_email_service_type,
            "auto_email_service_id": settings.registration_auto_email_service_id,
            "auto_proxy": settings.registration_auto_proxy,
            "auto_interval_min": settings.registration_auto_interval_min,
            "auto_interval_max": settings.registration_auto_interval_max,
            "auto_concurrency": settings.registration_auto_concurrency,
            "auto_mode": settings.registration_auto_mode,
            "auto_cpa_service_id": settings.registration_auto_cpa_service_id,
        },
        "webui": {
            "host": settings.webui_host,
            "port": settings.webui_port,
            "debug": settings.debug,
            "has_access_password": bool(settings.webui_access_password and settings.webui_access_password.get_secret_value()),
        },
        "tempmail": {
            "enabled": settings.tempmail_enabled,
            "api_url": settings.tempmail_base_url,
            "base_url": settings.tempmail_base_url,
            "timeout": settings.tempmail_timeout,
            "max_retries": settings.tempmail_max_retries,
        },
        "yyds_mail": {
            "enabled": settings.yyds_mail_enabled,
            "api_url": settings.yyds_mail_base_url,
            "base_url": settings.yyds_mail_base_url,
            "default_domain": settings.yyds_mail_default_domain,
            "timeout": settings.yyds_mail_timeout,
            "max_retries": settings.yyds_mail_max_retries,
            "has_api_key": bool(settings.yyds_mail_api_key and settings.yyds_mail_api_key.get_secret_value()),
        },
        "email_code": {
            "timeout": settings.email_code_timeout,
            "poll_interval": settings.email_code_poll_interval,
        },
    }


@router.get("/auto-quick-refresh")
def get_auto_quick_refresh_settings():
    settings = get_settings()
    from ..auto_quick_refresh_scheduler import auto_quick_refresh_scheduler

    runtime = auto_quick_refresh_scheduler.snapshot()
    return {
        "enabled": bool(settings.auto_quick_refresh_enabled),
        "interval_minutes": int(settings.auto_quick_refresh_interval_minutes),
        "retry_limit": int(settings.auto_quick_refresh_retry_limit),
        "runtime": runtime,
    }


@router.post("/auto-quick-refresh")
def update_auto_quick_refresh_settings(request: AutoQuickRefreshSettingsRequest):
    from ..auto_quick_refresh_scheduler import (
        AUTO_MAX_RETRY_LIMIT,
        AUTO_MAX_INTERVAL_MINUTES,
        AUTO_MIN_INTERVAL_MINUTES,
        auto_quick_refresh_scheduler,
    )

    interval_minutes = int(request.interval_minutes)
    retry_limit = int(request.retry_limit)

    if interval_minutes < AUTO_MIN_INTERVAL_MINUTES or interval_minutes > AUTO_MAX_INTERVAL_MINUTES:
        raise HTTPException(
            status_code=400,
            detail=f"interval_minutes must be between {AUTO_MIN_INTERVAL_MINUTES} and {AUTO_MAX_INTERVAL_MINUTES}",
        )
    if retry_limit < 0 or retry_limit > AUTO_MAX_RETRY_LIMIT:
        raise HTTPException(
            status_code=400,
            detail=f"retry_limit must be between 0 and {AUTO_MAX_RETRY_LIMIT}",
        )

    update_settings(
        auto_quick_refresh_enabled=bool(request.enabled),
        auto_quick_refresh_interval_minutes=interval_minutes,
        auto_quick_refresh_retry_limit=retry_limit,
    )

    runtime = auto_quick_refresh_scheduler.notify_schedule_updated()
    if request.enabled and bool(request.run_now):
        runtime = auto_quick_refresh_scheduler.request_run_now(reason="settings_save")

    return {
        "success": True,
        "enabled": bool(request.enabled),
        "interval_minutes": interval_minutes,
        "retry_limit": retry_limit,
        "runtime": runtime,
    }


@router.get("/proxy/dynamic")
def get_dynamic_proxy_settings():
    """获取动态代理设置"""
    settings = get_settings()
    return {
        "enabled": settings.proxy_dynamic_enabled,
        "api_url": settings.proxy_dynamic_api_url,
        "api_key_header": settings.proxy_dynamic_api_key_header,
        "result_field": settings.proxy_dynamic_result_field,
        "has_api_key": bool(settings.proxy_dynamic_api_key and settings.proxy_dynamic_api_key.get_secret_value()),
    }


class DynamicProxySettings(BaseModel):
    """动态代理设置"""
    enabled: bool = False
    api_url: str = ""
    api_key: Optional[str] = None
    api_key_header: str = "X-API-Key"
    result_field: str = ""


@router.post("/proxy/dynamic")
def update_dynamic_proxy_settings(request: DynamicProxySettings):
    """更新动态代理设置"""
    update_dict = {
        "proxy_dynamic_enabled": request.enabled,
        "proxy_dynamic_api_url": request.api_url,
        "proxy_dynamic_api_key_header": request.api_key_header,
        "proxy_dynamic_result_field": request.result_field,
    }
    if request.api_key is not None:
        update_dict["proxy_dynamic_api_key"] = request.api_key

    update_settings(**update_dict)
    return {"success": True, "message": "动态代理设置已更新"}


@router.post("/proxy/dynamic/test")
def test_dynamic_proxy(request: DynamicProxySettings):
    """测试动态代理 API"""
    from ...core.dynamic_proxy import fetch_dynamic_proxy

    if not request.api_url:
        raise HTTPException(status_code=400, detail="请填写动态代理 API 地址")

    # 若未传入 api_key，使用已保存的
    api_key = request.api_key or ""
    if not api_key:
        settings = get_settings()
        if settings.proxy_dynamic_api_key:
            api_key = settings.proxy_dynamic_api_key.get_secret_value()

    try:
        proxy_url = fetch_dynamic_proxy(
            api_url=request.api_url,
            api_key=api_key,
            api_key_header=request.api_key_header,
            result_field=request.result_field,
        )
    except Exception as exc:
        logger.exception(
            "Dynamic proxy fetch failed unexpectedly",
            extra={"api_url": request.api_url},
        )
        return _build_proxy_route_error_result(request.api_url, exc, "dynamic_proxy_fetch_exception")

    if not proxy_url:
        return _json_safe_proxy_payload({"success": False, "message": "动态代理 API 返回为空或请求失败"})

    result = _safe_run_proxy_diagnostics(proxy_url)
    if result.get("success"):
        elapsed = result.get("response_time")
        result["message"] = f"动态代理可用，出口 IP: {result.get('ip') or 'unknown'}，响应时间: {elapsed}ms"
    return _json_safe_proxy_payload(result)


@router.get("/registration")
def get_registration_settings():
    """获取注册设置"""
    settings = get_settings()

    entry_flow_raw = str(settings.registration_entry_flow or "native").strip().lower()
    entry_flow = "abcard" if entry_flow_raw == "abcard" else "native"
    wait_strategy = normalize_registration_wait_strategy(getattr(settings, "registration_wait_strategy", "start"))

    return {
        "max_retries": settings.registration_max_retries,
        "timeout": settings.registration_timeout,
        "default_password_length": settings.registration_default_password_length,
        "sleep_min": settings.registration_sleep_min,
        "sleep_max": settings.registration_sleep_max,
        "wait_strategy": wait_strategy,
        "entry_flow": entry_flow,
        "auto_enabled": settings.registration_auto_enabled,
        "auto_check_interval": settings.registration_auto_check_interval,
        "auto_min_ready_auth_files": settings.registration_auto_min_ready_auth_files,
        "auto_email_service_type": settings.registration_auto_email_service_type,
        "auto_email_service_id": settings.registration_auto_email_service_id,
        "auto_proxy": settings.registration_auto_proxy,
        "auto_interval_min": settings.registration_auto_interval_min,
        "auto_interval_max": settings.registration_auto_interval_max,
        "auto_concurrency": settings.registration_auto_concurrency,
        "auto_mode": settings.registration_auto_mode,
        "auto_cpa_service_id": settings.registration_auto_cpa_service_id,
    }


@router.post("/registration")
def update_registration_settings(request: RegistrationSettings):
    """更新注册设置"""
    if request.timeout < 30 or request.timeout > 600:
        raise HTTPException(status_code=400, detail="注册超时时间必须在 30-600 秒之间")

    if request.default_password_length < 8 or request.default_password_length > 64:
        raise HTTPException(status_code=400, detail="密码长度必须在 8-64 之间")

    if request.sleep_min < 1 or request.sleep_max < request.sleep_min:
        raise HTTPException(status_code=400, detail="注册等待时间参数无效")

    wait_strategy_raw = str(request.wait_strategy or RegistrationWaitStrategy.START.value).strip().lower()
    if wait_strategy_raw not in {RegistrationWaitStrategy.START.value, RegistrationWaitStrategy.COMPLETION.value}:
        raise HTTPException(status_code=400, detail="等待策略仅支持 start / completion")
    wait_strategy = normalize_registration_wait_strategy(wait_strategy_raw)

    flow_raw = (request.entry_flow or "native").strip().lower()
    # 兼容旧前端历史值：outlook -> native（Outlook 邮箱会在运行时自动走 outlook 链路）。
    flow = "native" if flow_raw == "outlook" else flow_raw
    if flow not in {"native", "abcard"}:
        raise HTTPException(status_code=400, detail="entry_flow 仅支持 native / abcard")

    if request.auto_check_interval < 5 or request.auto_check_interval > 3600:
        raise HTTPException(status_code=400, detail="自动注册检查间隔必须在 5-3600 秒之间")

    if request.auto_min_ready_auth_files < 1 or request.auto_min_ready_auth_files > 10000:
        raise HTTPException(status_code=400, detail="自动注册保底数量必须在 1-10000 之间")

    try:
        EmailServiceType(request.auto_email_service_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="自动注册邮箱服务类型无效") from exc

    normalized_auto_email_service_type = (
        "imap_mail" if request.auto_email_service_type == "catchall_imap" else request.auto_email_service_type
    )

    if request.auto_interval_min < 0 or request.auto_interval_max < request.auto_interval_min:
        raise HTTPException(status_code=400, detail="自动注册间隔时间参数无效")

    if request.auto_concurrency < 1 or request.auto_concurrency > 100:
        raise HTTPException(status_code=400, detail="自动注册并发数必须在 1-100 之间")

    if request.auto_mode not in ("parallel", "pipeline"):
        raise HTTPException(status_code=400, detail="自动注册模式必须为 parallel 或 pipeline")

    if request.auto_enabled and request.auto_cpa_service_id <= 0:
        raise HTTPException(status_code=400, detail="启用自动注册时必须选择一个 CPA 服务")

    with get_db() as db:
        if request.auto_enabled:
            cpa_service = crud.get_cpa_service_by_id(db, request.auto_cpa_service_id)
            if not cpa_service or not cpa_service.enabled:
                raise HTTPException(status_code=400, detail="自动注册选择的 CPA 服务不存在或已禁用")

        if request.auto_email_service_id > 0:
            email_service = crud.get_email_service_by_id(db, request.auto_email_service_id)
            if not email_service or not email_service.enabled:
                raise HTTPException(status_code=400, detail="自动注册选择的邮箱服务不存在或已禁用")
            normalized_service_type = (
                "imap_mail" if email_service.service_type == "catchall_imap" else email_service.service_type
            )
            if normalized_service_type != normalized_auto_email_service_type:
                raise HTTPException(status_code=400, detail="自动注册邮箱服务类型与指定服务不匹配")

    update_settings(
        registration_max_retries=request.max_retries,
        registration_timeout=request.timeout,
        registration_default_password_length=request.default_password_length,
        registration_sleep_min=request.sleep_min,
        registration_sleep_max=request.sleep_max,
        registration_wait_strategy=wait_strategy,
        registration_entry_flow=flow,
        registration_auto_enabled=request.auto_enabled,
        registration_auto_check_interval=request.auto_check_interval,
        registration_auto_min_ready_auth_files=request.auto_min_ready_auth_files,
        registration_auto_email_service_type=normalized_auto_email_service_type,
        registration_auto_email_service_id=max(0, request.auto_email_service_id),
        registration_auto_proxy=(request.auto_proxy or "").strip(),
        registration_auto_interval_min=request.auto_interval_min,
        registration_auto_interval_max=request.auto_interval_max,
        registration_auto_concurrency=request.auto_concurrency,
        registration_auto_mode=request.auto_mode,
        registration_auto_cpa_service_id=max(0, request.auto_cpa_service_id),
    )

    if request.auto_enabled:
        update_auto_registration_state(
            enabled=True,
            status="checking",
            message="自动注册设置已更新，正在立即检查库存",
            target_ready_count=request.auto_min_ready_auth_files,
        )
        trigger_auto_registration_check()
    else:
        update_auto_registration_state(
            enabled=False,
            status="disabled",
            message="自动注册已禁用",
            current_batch_id=None,
            current_ready_count=None,
            target_ready_count=request.auto_min_ready_auth_files,
        )

    return {"success": True, "message": "注册设置已更新"}


@router.post("/webui")
def update_webui_settings(request: WebUISettings):
    """更新 Web UI 设置"""
    update_dict = {}
    if request.host is not None:
        update_dict["webui_host"] = request.host
    if request.port is not None:
        update_dict["webui_port"] = request.port
    if request.debug is not None:
        update_dict["debug"] = request.debug
    if request.access_password:
        update_dict["webui_access_password"] = request.access_password

    update_settings(**update_dict)
    return {"success": True, "message": "Web UI 设置已更新"}


@router.get("/database")
def get_database_info():
    """获取数据库信息"""
    settings = get_settings()

    import os
    from pathlib import Path

    db_path = settings.database_url
    if db_path.startswith("sqlite:///"):
        db_path = db_path[10:]

    db_file = Path(db_path) if os.path.isabs(db_path) else Path(db_path)
    db_size = db_file.stat().st_size if db_file.exists() else 0

    with get_db() as db:
        from ...database.models import Account, EmailService, RegistrationTask

        account_count = db.query(Account).count()
        service_count = db.query(EmailService).count()
        task_count = db.query(RegistrationTask).count()

    return {
        "database_url": settings.database_url,
        "database_size_bytes": db_size,
        "database_size_mb": round(db_size / (1024 * 1024), 2),
        "accounts_count": account_count,
        "email_services_count": service_count,
        "tasks_count": task_count,
    }


@router.post("/database/backup")
def backup_database():
    """备份数据库"""
    import shutil
    from datetime import datetime

    settings = get_settings()

    db_path = settings.database_url
    if db_path.startswith("sqlite:///"):
        db_path = db_path[10:]

    if not os.path.exists(db_path):
        raise HTTPException(status_code=404, detail="数据库文件不存在")

    # 创建备份目录
    from pathlib import Path as FilePath
    backup_dir = FilePath(db_path).parent / "backups"
    backup_dir.mkdir(exist_ok=True)

    # 生成备份文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"database_backup_{timestamp}.db"

    # 复制数据库文件
    shutil.copy2(db_path, backup_path)

    return {
        "success": True,
        "message": "数据库备份成功",
        "backup_path": str(backup_path)
    }


@router.post("/database/import")
async def import_database(file: UploadFile = File(...)):
    """导入数据库（自动备份后覆盖当前 SQLite 文件）"""
    import shutil
    import tempfile
    from datetime import datetime
    from pathlib import Path as FilePath
    from ...database.session import get_session_manager

    settings = get_settings()

    db_path = settings.database_url
    if not db_path.startswith("sqlite:///"):
        raise HTTPException(status_code=400, detail="当前仅支持 SQLite 数据库导入")

    db_path = db_path[10:]
    db_file = FilePath(db_path)

    # 校验上传扩展名
    filename = (file.filename or "").lower()
    allowed_ext = (".db", ".sqlite", ".sqlite3")
    if filename and not filename.endswith(allowed_ext):
        raise HTTPException(status_code=400, detail="仅支持 .db / .sqlite / .sqlite3 文件")

    if not db_file.exists():
        raise HTTPException(status_code=404, detail="数据库文件不存在")

    # 先落地到临时文件，再校验头，避免脏写
    temp_path = None
    try:
        db_file.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix="db_import_",
            suffix=".db",
            dir=str(db_file.parent),
            delete=False
        ) as tmp:
            temp_path = FilePath(tmp.name)
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                tmp.write(chunk)

        if not temp_path.exists() or temp_path.stat().st_size < 100:
            raise HTTPException(status_code=400, detail="导入文件无效或为空")

        # SQLite 文件头校验
        with temp_path.open("rb") as f:
            header = f.read(16)
        if not header.startswith(b"SQLite format 3\x00"):
            raise HTTPException(status_code=400, detail="文件不是有效的 SQLite 数据库")

        # 先释放数据库连接，避免 Windows 下文件被占用
        session_manager = get_session_manager()
        session_manager.engine.dispose()

        # 导入前自动备份
        backup_dir = db_file.parent / "backups"
        backup_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"database_backup_before_import_{timestamp}.db"
        shutil.copy2(db_file, backup_path)

        # 清理 WAL/SHM，避免替换后出现旧事务残留
        wal_file = FilePath(f"{db_file}-wal")
        shm_file = FilePath(f"{db_file}-shm")
        for sidecar in (wal_file, shm_file):
            try:
                if sidecar.exists():
                    sidecar.unlink()
            except Exception:
                logger.warning("清理 SQLite 附属文件失败: %s", sidecar)

        os.replace(str(temp_path), str(db_file))

        logger.info("数据库导入成功: file=%s backup=%s", file.filename, backup_path)
        return {
            "success": True,
            "message": "数据库导入成功",
            "backup_path": str(backup_path),
        }
    finally:
        await file.close()
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass


@router.post("/database/cleanup")
def cleanup_database(
    days: int = 30,
    keep_failed: bool = True
):
    """清理过期数据"""
    from datetime import timedelta
    from ...core.timezone_utils import utcnow_naive

    cutoff_date = utcnow_naive() - timedelta(days=days)

    with get_db() as db:
        from ...database.models import RegistrationTask
        from sqlalchemy import delete

        # 删除旧任务
        conditions = [RegistrationTask.created_at < cutoff_date]
        if not keep_failed:
            conditions.append(RegistrationTask.status != "failed")
        else:
            conditions.append(RegistrationTask.status.in_(["completed", "cancelled"]))

        result = db.execute(
            delete(RegistrationTask).where(*conditions)
        )
        db.commit()

        deleted_count = result.rowcount

    return {
        "success": True,
        "message": f"已清理 {deleted_count} 条过期任务记录",
        "deleted_count": deleted_count
    }


@router.get("/logs")
def get_recent_logs(
    lines: int = 100,
    level: str = "INFO"
):
    """获取最近日志"""
    settings = get_settings()

    log_file = settings.log_file
    if not log_file:
        return {"logs": [], "message": "日志文件未配置"}

    from pathlib import Path
    log_path = Path(log_file)

    if not log_path.exists():
        return {"logs": [], "message": "日志文件不存在"}

    try:
        with open(log_path, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
            recent_lines = all_lines[-lines:]

        return {
            "logs": [line.strip() for line in recent_lines],
            "total_lines": len(all_lines)
        }
    except Exception as e:
        return {"logs": [], "error": str(e)}


# ============== 临时邮箱设置 ==============

class TempmailSettings(BaseModel):
    """临时邮箱设置"""
    api_url: Optional[str] = None
    enabled: Optional[bool] = None
    yyds_api_url: Optional[str] = None
    yyds_api_key: Optional[str] = None
    yyds_default_domain: Optional[str] = None
    yyds_enabled: Optional[bool] = None


class EmailCodeSettings(BaseModel):
    """验证码等待设置"""
    timeout: int = 120  # 验证码等待超时（秒）
    poll_interval: int = 3  # 验证码轮询间隔（秒）


@router.get("/tempmail")
def get_tempmail_settings():
    """获取临时邮箱设置"""
    settings = get_settings()

    return {
        "tempmail": {
            "api_url": settings.tempmail_base_url,
            "timeout": settings.tempmail_timeout,
            "max_retries": settings.tempmail_max_retries,
            "enabled": settings.tempmail_enabled,
        },
        "yyds_mail": {
            "api_url": settings.yyds_mail_base_url,
            "default_domain": settings.yyds_mail_default_domain,
            "timeout": settings.yyds_mail_timeout,
            "max_retries": settings.yyds_mail_max_retries,
            "enabled": settings.yyds_mail_enabled,
            "has_api_key": bool(settings.yyds_mail_api_key and settings.yyds_mail_api_key.get_secret_value()),
        },
    }


@router.post("/tempmail")
def update_tempmail_settings(request: TempmailSettings):
    """更新临时邮箱设置"""
    update_dict = {}

    if request.api_url:
        update_dict["tempmail_base_url"] = request.api_url
    if request.enabled is not None:
        update_dict["tempmail_enabled"] = request.enabled
    if request.yyds_api_url is not None:
        update_dict["yyds_mail_base_url"] = request.yyds_api_url
    if request.yyds_api_key is not None:
        update_dict["yyds_mail_api_key"] = request.yyds_api_key
    if request.yyds_default_domain is not None:
        update_dict["yyds_mail_default_domain"] = request.yyds_default_domain
    if request.yyds_enabled is not None:
        update_dict["yyds_mail_enabled"] = request.yyds_enabled

    update_settings(**update_dict)

    return {"success": True, "message": "临时邮箱设置已更新"}


# ============== 验证码等待设置 ==============

@router.get("/email-code")
def get_email_code_settings():
    """获取验证码等待设置"""
    settings = get_settings()
    return {
        "timeout": settings.email_code_timeout,
        "poll_interval": settings.email_code_poll_interval,
    }


@router.post("/email-code")
def update_email_code_settings(request: EmailCodeSettings):
    """更新验证码等待设置"""
    # 验证参数范围
    if request.timeout < 30 or request.timeout > 600:
        raise HTTPException(status_code=400, detail="超时时间必须在 30-600 秒之间")
    if request.poll_interval < 1 or request.poll_interval > 30:
        raise HTTPException(status_code=400, detail="轮询间隔必须在 1-30 秒之间")

    update_settings(
        email_code_timeout=request.timeout,
        email_code_poll_interval=request.poll_interval,
    )

    return {"success": True, "message": "验证码等待设置已更新"}


# ============== 代理列表 CRUD ==============

class ProxyCreateRequest(BaseModel):
    """创建代理请求"""
    name: str
    type: str = "http"  # http, socks5
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    enabled: bool = True
    priority: int = 0


class ProxyUpdateRequest(BaseModel):
    """更新代理请求"""
    name: Optional[str] = None
    type: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    enabled: Optional[bool] = None
    priority: Optional[int] = None


@router.get("/proxies")
def get_proxies_list(enabled: Optional[bool] = None):
    """获取代理列表"""
    with get_db() as db:
        proxies = crud.get_proxies(db, enabled=enabled)
        return _json_safe_proxy_payload({
            "proxies": [_safe_proxy_to_dict(p) for p in proxies],
            "total": len(proxies)
        })


@router.post("/proxies")
def create_proxy_item(request: ProxyCreateRequest):
    """创建代理"""
    payload = _normalize_proxy_payload(
        request.type,
        request.host,
        request.port,
        request.username,
        request.password,
    )
    with get_db() as db:
        proxy = crud.create_proxy(
            db,
            name=request.name,
            type=payload["type"],
            host=payload["host"],
            port=payload["port"],
            username=payload["username"],
            password=payload["password"],
            enabled=request.enabled,
            priority=request.priority
        )
        return _json_safe_proxy_payload({"success": True, "proxy": _safe_proxy_to_dict(proxy)})


@router.get("/proxies/{proxy_id}")
def get_proxy_item(proxy_id: int):
    """获取单个代理"""
    with get_db() as db:
        proxy = crud.get_proxy_by_id(db, proxy_id)
        if not proxy:
            raise HTTPException(status_code=404, detail="代理不存在")
        return _json_safe_proxy_payload(_safe_proxy_to_dict(proxy, include_password=True))


@router.patch("/proxies/{proxy_id}")
def update_proxy_item(proxy_id: int, request: ProxyUpdateRequest):
    """更新代理"""
    with get_db() as db:
        existing_proxy = crud.get_proxy_by_id(db, proxy_id)
        if not existing_proxy:
            raise HTTPException(status_code=404, detail="代理不存在")

        update_data = {}
        if request.name is not None:
            update_data["name"] = request.name
        if request.enabled is not None:
            update_data["enabled"] = request.enabled
        if request.priority is not None:
            update_data["priority"] = request.priority

        if any(
            value is not None
            for value in (
                request.type,
                request.host,
                request.port,
                request.username,
                request.password,
            )
        ):
            host_supplies_auth = bool(request.host is not None and "://" in str(request.host))
            normalized = _normalize_proxy_payload(
                request.type if request.type is not None else existing_proxy.type,
                request.host if request.host is not None else existing_proxy.host,
                request.port if request.port is not None else existing_proxy.port,
                request.username if request.username is not None else (None if host_supplies_auth else existing_proxy.username),
                request.password if request.password is not None else (None if host_supplies_auth else existing_proxy.password),
            )
            update_data.update(normalized)

        proxy = crud.update_proxy(db, proxy_id, **update_data)
        return _json_safe_proxy_payload({"success": True, "proxy": _safe_proxy_to_dict(proxy)})


@router.delete("/proxies/{proxy_id}")
def delete_proxy_item(proxy_id: int):
    """删除代理"""
    with get_db() as db:
        success = crud.delete_proxy(db, proxy_id)
        if not success:
            raise HTTPException(status_code=404, detail="代理不存在")
        return {"success": True, "message": "代理已删除"}


@router.post("/proxies/{proxy_id}/set-default")
def set_proxy_default(proxy_id: int):
    """将指定代理设为默认"""
    with get_db() as db:
        proxy = crud.set_proxy_default(db, proxy_id)
        if not proxy:
            raise HTTPException(status_code=404, detail="代理不存在")
        return _json_safe_proxy_payload({"success": True, "proxy": _safe_proxy_to_dict(proxy)})


@router.post("/proxies/{proxy_id}/test")
def test_proxy_item(proxy_id: int):
    """测试单个代理"""
    try:
        with get_db() as db:
            proxy = crud.get_proxy_by_id(db, proxy_id)
            if not proxy:
                raise HTTPException(status_code=404, detail="代理不存在")
            return _json_safe_proxy_payload(_safe_run_proxy_diagnostics(proxy.proxy_url))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Proxy test route failed unexpectedly", extra={"proxy_id": proxy_id})
        return _json_safe_proxy_payload(_build_proxy_route_error_result("", exc, "proxy_test_route_exception"))


@router.post("/proxies/test-all")
def test_all_proxies():
    """测试所有启用的代理"""
    try:
        with get_db() as db:
            proxies = crud.get_enabled_proxies(db)

            results = []
            for proxy in proxies:
                result = _safe_run_proxy_diagnostics(proxy.proxy_url)
                results.append({"id": proxy.id, "name": proxy.name, **result})

            success_count = sum(1 for r in results if r.get("success"))
            return _json_safe_proxy_payload({
                "total": len(proxies),
                "success": success_count,
                "failed": len(proxies) - success_count,
                "results": results
            })
    except Exception as exc:
        logger.exception("Batch proxy test failed unexpectedly")
        route_error = _build_proxy_route_error_result("", exc, "proxy_test_all_route_exception")
        return _json_safe_proxy_payload({
            "total": 0,
            "success": 0,
            "failed": 0,
            "results": [route_error],
        })


@router.post("/proxies/{proxy_id}/enable")
def enable_proxy(proxy_id: int):
    """启用代理"""
    with get_db() as db:
        proxy = crud.update_proxy(db, proxy_id, enabled=True)
        if not proxy:
            raise HTTPException(status_code=404, detail="代理不存在")
        return {"success": True, "message": "代理已启用"}


@router.post("/proxies/{proxy_id}/disable")
def disable_proxy(proxy_id: int):
    """禁用代理"""
    with get_db() as db:
        proxy = crud.update_proxy(db, proxy_id, enabled=False)
        if not proxy:
            raise HTTPException(status_code=404, detail="代理不存在")
        return {"success": True, "message": "代理已禁用"}


# ============== Outlook 设置 ==============

class OutlookSettings(BaseModel):
    """Outlook 设置"""
    default_client_id: Optional[str] = None


@router.get("/outlook")
def get_outlook_settings():
    """获取 Outlook 设置"""
    settings = get_settings()

    return {
        "default_client_id": settings.outlook_default_client_id,
        "provider_priority": settings.outlook_provider_priority,
        "health_failure_threshold": settings.outlook_health_failure_threshold,
        "health_disable_duration": settings.outlook_health_disable_duration,
    }


@router.post("/outlook")
def update_outlook_settings(request: OutlookSettings):
    """更新 Outlook 设置"""
    update_dict = {}

    if request.default_client_id is not None:
        update_dict["outlook_default_client_id"] = request.default_client_id

    if update_dict:
        update_settings(**update_dict)

    return {"success": True, "message": "Outlook 设置已更新"}


# ============== Team Manager 设置 ==============

class TeamManagerSettings(BaseModel):
    """Team Manager 设置"""
    enabled: bool = False
    api_url: str = ""
    api_key: str = ""


class TeamManagerTestRequest(BaseModel):
    """Team Manager 测试请求"""
    api_url: str
    api_key: str


@router.get("/team-manager")
def get_team_manager_settings():
    """获取 Team Manager 设置"""
    settings = get_settings()
    return {
        "enabled": settings.tm_enabled,
        "api_url": settings.tm_api_url,
        "has_api_key": bool(settings.tm_api_key and settings.tm_api_key.get_secret_value()),
    }


@router.post("/team-manager")
def update_team_manager_settings(request: TeamManagerSettings):
    """更新 Team Manager 设置"""
    update_dict = {
        "tm_enabled": request.enabled,
        "tm_api_url": request.api_url,
    }
    if request.api_key:
        update_dict["tm_api_key"] = request.api_key
    update_settings(**update_dict)
    return {"success": True, "message": "Team Manager 设置已更新"}


@router.post("/team-manager/test")
def test_team_manager_connection(request: TeamManagerTestRequest):
    """测试 Team Manager 连接"""
    from ...core.upload.team_manager_upload import test_team_manager_connection as do_test

    settings = get_settings()
    api_key = request.api_key
    if api_key == 'use_saved_key' or not api_key:
        if settings.tm_api_key:
            api_key = settings.tm_api_key.get_secret_value()
        else:
            return {"success": False, "message": "未配置 API Key"}

    success, message = do_test(request.api_url, api_key)
    return {"success": success, "message": message}
