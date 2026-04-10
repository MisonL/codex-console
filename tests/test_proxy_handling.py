from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace

from src.core.http_client import HTTPClient
from src.core.proxy_utils import diagnose_proxy_failure, normalize_proxy_url
from src.database.models import Proxy
from src.web.routes import settings as settings_routes


@contextmanager
def _fake_get_db():
    yield object()


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


def test_proxy_model_normalizes_legacy_full_url_host():
    proxy = Proxy(
        name="webshare",
        type="http",
        host="http://user:pass@p.webshare.io",
        port=80,
    )

    assert proxy.proxy_url == "http://user:pass@p.webshare.io:80"

    data = proxy.to_dict(include_password=True)
    assert data["host"] == "p.webshare.io"
    assert data["username"] == "user"
    assert data["password"] == "pass"


def test_proxy_model_to_dict_handles_legacy_mixed_types():
    proxy = Proxy(
        name="legacy",
        type="http",
        host="p.webshare.io",
        port=80,
        username="user",
        password="pass",
    )
    proxy.last_used = "2026-04-08 12:00:00"
    proxy.created_at = datetime(2026, 4, 8, 12, 0, 0)
    proxy.updated_at = {"unexpected": "value"}

    payload = proxy.to_dict(include_password=True)

    assert payload["host"] == "p.webshare.io"
    assert payload["port"] == 80
    assert payload["password"] == "pass"
    assert payload["last_used"] == "2026-04-08 12:00:00"
    assert payload["created_at"].startswith("2026-04-08T12:00:00")
    assert payload["updated_at"] == "{'unexpected': 'value'}"


def test_normalize_proxy_url_recovers_nested_scheme():
    value = normalize_proxy_url("http://http://user:pass@p.webshare.io:80")
    assert value == "http://user:pass@p.webshare.io:80"


def test_http_client_normalizes_proxy_url():
    client = HTTPClient(proxy_url="http://http://user:pass@p.webshare.io:80")
    assert client.proxy_url == "http://user:pass@p.webshare.io:80"
    assert client.proxies == {
        "http": "http://user:pass@p.webshare.io:80",
        "https": "http://user:pass@p.webshare.io:80",
    }


def test_create_proxy_item_splits_full_proxy_url(monkeypatch):
    captured = {}

    def fake_create_proxy(db, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(to_dict=lambda include_password=False: {"id": 1, **kwargs})

    monkeypatch.setattr(settings_routes, "get_db", _fake_get_db)
    monkeypatch.setattr(settings_routes.crud, "create_proxy", fake_create_proxy)

    request = settings_routes.ProxyCreateRequest(
        name="webshare",
        type="http",
        host="http://user:pass@p.webshare.io",
        port=80,
        username=None,
        password=None,
    )

    result = settings_routes.create_proxy_item(request)

    assert result["success"] is True
    assert captured["host"] == "p.webshare.io"
    assert captured["port"] == 80
    assert captured["username"] == "user"
    assert captured["password"] == "pass"


def test_create_proxy_item_keeps_hyphenated_username(monkeypatch):
    captured = {}

    def fake_create_proxy(db, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(to_dict=lambda include_password=False: {"id": 1, **kwargs})

    monkeypatch.setattr(settings_routes, "get_db", _fake_get_db)
    monkeypatch.setattr(settings_routes.crud, "create_proxy", fake_create_proxy)

    request = settings_routes.ProxyCreateRequest(
        name="webshare",
        type="http",
        host="http://yiiwsaoy-rotate:secret@p.webshare.io",
        port=80,
        username=None,
        password=None,
    )

    result = settings_routes.create_proxy_item(request)

    assert result["success"] is True
    assert captured["host"] == "p.webshare.io"
    assert captured["username"] == "yiiwsaoy-rotate"
    assert captured["password"] == "secret"


def test_update_proxy_item_reuses_existing_and_normalizes(monkeypatch):
    existing_proxy = SimpleNamespace(
        type="http",
        host="legacy.example",
        port=8080,
        username="old-user",
        password="old-pass",
    )
    captured = {}

    def fake_update_proxy(db, proxy_id, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(to_dict=lambda include_password=False: {"id": proxy_id, **kwargs})

    monkeypatch.setattr(settings_routes, "get_db", _fake_get_db)
    monkeypatch.setattr(settings_routes.crud, "get_proxy_by_id", lambda db, proxy_id: existing_proxy)
    monkeypatch.setattr(settings_routes.crud, "update_proxy", fake_update_proxy)

    request = settings_routes.ProxyUpdateRequest(
        host="http://user:pass@p.webshare.io",
        port=80,
    )

    result = settings_routes.update_proxy_item(1, request)

    assert result["success"] is True
    assert captured["host"] == "p.webshare.io"
    assert captured["port"] == 80
    assert captured["username"] == "user"
    assert captured["password"] == "pass"


def test_probe_proxy_endpoint_fallback_to_requests_when_cffi_failed(monkeypatch):
    def _raise_connect_aborted(*args, **kwargs):
        raise RuntimeError("Failed to perform, curl: (56) Proxy CONNECT aborted")

    def _requests_ok(*args, **kwargs):
        return _FakeResponse(status_code=200, payload={"ip": "1.1.1.1"})

    monkeypatch.setattr(settings_routes, "_request_via_curl_cffi", _raise_connect_aborted)
    monkeypatch.setattr(settings_routes, "_request_via_requests", _requests_ok)

    result = settings_routes._probe_proxy_endpoint(
        "http://user:pass@p.webshare.io:80",
        "https://api.ipify.org?format=json",
        "exit_ip",
    )

    assert result["success"] is True
    assert result["transport"] == "requests"
    assert result["diagnosis_code"] == "curl_cffi_tls_incompatible"
    assert "TLS 仿真" in result["message"]
    assert result["fallback_probe"]["transport"] == "curl_cffi"


def test_probe_proxy_endpoint_error56_adds_webshare_ip_hint(monkeypatch):
    def _raise_connect_aborted(*args, **kwargs):
        raise RuntimeError("Failed to perform, curl: (56) Proxy CONNECT aborted")

    monkeypatch.setattr(settings_routes, "_request_via_curl_cffi", _raise_connect_aborted)
    monkeypatch.setattr(settings_routes, "_request_via_requests", _raise_connect_aborted)
    monkeypatch.setattr(settings_routes, "_resolve_server_public_ip", lambda: "47.129.244.169")

    result = settings_routes._probe_proxy_endpoint(
        "http://user:pass@p.webshare.io:80",
        "https://api.ipify.org?format=json",
        "exit_ip",
    )

    assert result["success"] is False
    assert "请检查 Webshare 后台是否已授权当前服务器 IP (47.129.244.169)" in result["message"]
    assert result["diagnosis_code"] == "proxy_connect_aborted"


def test_probe_proxy_endpoint_error56_without_public_ip_adds_fallback_hint(monkeypatch):
    def _raise_connect_aborted(*args, **kwargs):
        raise RuntimeError("Failed to perform, curl: (56) Proxy CONNECT aborted")

    monkeypatch.setattr(settings_routes, "_request_via_curl_cffi", _raise_connect_aborted)
    monkeypatch.setattr(settings_routes, "_request_via_requests", _raise_connect_aborted)
    monkeypatch.setattr(settings_routes, "_resolve_server_public_ip", lambda: "")

    result = settings_routes._probe_proxy_endpoint(
        "http://user:pass@p.webshare.io:80",
        "https://api.ipify.org?format=json",
        "exit_ip",
    )

    assert result["success"] is False
    assert "请检查 Webshare 后台是否已授权当前服务器 IP" in result["message"]
    assert result["diagnosis_code"] == "proxy_connect_aborted"


def test_probe_proxy_endpoint_handles_unexpected_exception(monkeypatch):
    def _raise_unexpected(*args, **kwargs):
        raise RuntimeError("probe transport exploded")

    monkeypatch.setattr(settings_routes, "_probe_proxy_with_transport", _raise_unexpected)

    result = settings_routes._probe_proxy_endpoint(
        "http://user:pass@p.webshare.io:80",
        "https://api.ipify.org?format=json",
        "exit_ip",
    )

    assert result["success"] is False
    assert result["diagnosis_code"] == "proxy_probe_exception"
    assert "probe transport exploded" in result["message"]


def test_probe_proxy_with_transport_invalid_proxy_url_not_direct_fallback(monkeypatch):
    called = {"value": False}

    def _request_func(*args, **kwargs):
        called["value"] = True
        return _FakeResponse(status_code=200)

    monkeypatch.setattr(settings_routes, "build_requests_proxy_map", lambda *_args, **_kwargs: None)

    result = settings_routes._probe_proxy_with_transport(
        "invalid-proxy",
        "https://api.ipify.org?format=json",
        "exit_ip",
        "requests",
        _request_func,
    )

    assert result["success"] is False
    assert result["diagnosis_code"] == "invalid_proxy_url"
    assert called["value"] is False


def test_run_proxy_diagnostics_handles_unexpected_exception(monkeypatch):
    def _raise_probe(*args, **kwargs):
        raise RuntimeError("diagnostics exploded")

    monkeypatch.setattr(settings_routes, "_probe_proxy_endpoint", _raise_probe)

    result = settings_routes._run_proxy_diagnostics("http://user:pass@p.webshare.io:80")

    assert result["success"] is False
    assert result["diagnosis_code"] == "proxy_diagnostics_exception"
    assert "代理诊断执行失败" in result["message"]
    assert "diagnostics exploded" in result["error"]


def test_run_proxy_diagnostics_marks_exit_ip_only_as_success(monkeypatch):
    def _fake_probe(_proxy_url, target_url, label):
        if label == "exit_ip":
            return {
                "name": label,
                "url": target_url,
                "transport": "curl_cffi",
                "success": True,
                "status_code": 200,
                "elapsed_ms": 120,
                "response": _FakeResponse(status_code=200, payload={"ip": "47.129.244.169"}),
            }
        return {
            "name": label,
            "url": target_url,
            "transport": "curl_cffi",
            "success": False,
            "status_code": 403,
            "elapsed_ms": 95,
            "diagnosis_code": "target_forbidden",
            "message": "目标站点拒绝了当前请求",
        }

    monkeypatch.setattr(settings_routes, "_probe_proxy_endpoint", _fake_probe)
    monkeypatch.setattr(settings_routes, "_resolve_server_public_ip", lambda: "8.8.8.8")

    result = settings_routes._run_proxy_diagnostics("http://user:pass@p.webshare.io:80")

    assert result["success"] is True
    assert result["diagnosis_code"] == "proxy_exit_ip_only"
    assert result["warning_diagnosis_code"] == "target_forbidden"
    assert result["ip"] == "47.129.244.169"
    assert "代理出口可用，出口 IP: 47.129.244.169" in result["message"]


def test_run_proxy_diagnostics_marks_leak_when_exit_ip_matches_server(monkeypatch):
    def _fake_probe(_proxy_url, target_url, label):
        return {
            "name": label,
            "url": target_url,
            "transport": "requests",
            "success": True,
            "status_code": 200,
            "elapsed_ms": 66,
            "response": _FakeResponse(status_code=200, payload={"ip": "47.129.244.169"}),
        }

    monkeypatch.setattr(settings_routes, "_probe_proxy_endpoint", _fake_probe)
    monkeypatch.setattr(settings_routes, "_resolve_server_public_ip", lambda: "47.129.244.169")

    result = settings_routes._run_proxy_diagnostics("http://user:pass@p.webshare.io:80")

    assert result["success"] is False
    assert result["diagnosis_code"] == "proxy_leak_detected"
    assert result["ip"] == "47.129.244.169"
    assert result["server_ip"] == "47.129.244.169"
    assert result["message"] == "警告：代理未生效，当前检测到的是服务器真实 IP，请检查代理配置格式。"


def test_run_proxy_diagnostics_rejects_success_when_server_public_ip_unavailable(monkeypatch):
    def _fake_probe(_proxy_url, target_url, label):
        if label == "exit_ip":
            return {
                "name": label,
                "url": target_url,
                "transport": "requests",
                "success": True,
                "status_code": 200,
                "elapsed_ms": 44,
                "response": _FakeResponse(status_code=200, payload={"ip": "1.1.1.1"}),
            }
        return {
            "name": label,
            "url": target_url,
            "transport": "requests",
            "success": True,
            "status_code": 200,
            "elapsed_ms": 33,
        }

    monkeypatch.setattr(settings_routes, "_probe_proxy_endpoint", _fake_probe)
    monkeypatch.setattr(settings_routes, "_resolve_server_public_ip", lambda: "")

    result = settings_routes._run_proxy_diagnostics("http://user:pass@p.webshare.io:80")

    assert result["success"] is False
    assert result["diagnosis_code"] == "server_public_ip_unavailable"
    assert result["ip"] == "1.1.1.1"
    assert "无法确认服务器公网 IP" in result["message"]


def test_run_proxy_diagnostics_rejects_success_when_exit_ip_missing(monkeypatch):
    def _fake_probe(_proxy_url, target_url, label):
        if label == "exit_ip":
            return {
                "name": label,
                "url": target_url,
                "transport": "requests",
                "success": True,
                "status_code": 200,
                "elapsed_ms": 44,
                "response": _FakeResponse(status_code=200, payload={}, text=""),
            }
        return {
            "name": label,
            "url": target_url,
            "transport": "requests",
            "success": True,
            "status_code": 200,
            "elapsed_ms": 33,
        }

    monkeypatch.setattr(settings_routes, "_probe_proxy_endpoint", _fake_probe)
    monkeypatch.setattr(settings_routes, "_resolve_server_public_ip", lambda: "47.129.244.169")

    result = settings_routes._run_proxy_diagnostics("http://user:pass@p.webshare.io:80")

    assert result["success"] is False
    assert result["diagnosis_code"] == "proxy_exit_ip_missing"
    assert "出口 IP" in result["message"]


def test_safe_run_proxy_diagnostics_normalizes_invalid_payload(monkeypatch):
    monkeypatch.setattr(settings_routes, "_run_proxy_diagnostics", lambda *_args, **_kwargs: "invalid")

    result = settings_routes._safe_run_proxy_diagnostics("http://user:pass@p.webshare.io:80")

    assert result["success"] is False
    assert result["diagnosis_code"] == "proxy_diagnostics_invalid_payload"


def test_safe_run_proxy_diagnostics_appends_response_time_without_dropping_warning(monkeypatch):
    monkeypatch.setattr(
        settings_routes,
        "_run_proxy_diagnostics",
        lambda *_args, **_kwargs: {
            "success": True,
            "ip": "1.1.1.1",
            "response_time": 42,
            "message": "代理出口可用，出口 IP: 1.1.1.1；但访问 OpenAI 认证站点失败。",
        },
    )

    result = settings_routes._safe_run_proxy_diagnostics("http://user:pass@p.webshare.io:80")

    assert result["success"] is True
    assert result["message"].startswith("代理出口可用，出口 IP: 1.1.1.1")
    assert result["message"].endswith("响应时间: 42ms")


def test_get_proxy_item_fallbacks_when_proxy_to_dict_raises(monkeypatch):
    class _BrokenProxy:
        id = 9
        name = "broken"
        type = "http"
        host = "proxy.local"
        port = 8080
        username = "user"
        password = "pass"
        enabled = True
        is_default = False
        priority = 1
        last_used = "2026-04-08 12:00:00"
        created_at = "2026-04-08 12:00:01"
        updated_at = "2026-04-08 12:00:02"

        def to_dict(self, include_password=False):
            raise TypeError("legacy conversion exploded")

    monkeypatch.setattr(settings_routes, "get_db", _fake_get_db)
    monkeypatch.setattr(settings_routes.crud, "get_proxy_by_id", lambda *_args, **_kwargs: _BrokenProxy())

    result = settings_routes.get_proxy_item(9)

    assert result["id"] == 9
    assert result["host"] == "proxy.local"
    assert result["port"] == 8080
    assert result["username"] == "user"
    assert result["password"] == "pass"
    assert result["last_used"] == "2026-04-08 12:00:00"


def test_get_proxy_item_json_safe_even_when_serializer_returns_custom_object(monkeypatch):
    class _Proxy:
        pass

    monkeypatch.setattr(settings_routes, "get_db", _fake_get_db)
    monkeypatch.setattr(settings_routes.crud, "get_proxy_by_id", lambda *_args, **_kwargs: _Proxy())
    monkeypatch.setattr(
        settings_routes,
        "_safe_proxy_to_dict",
        lambda *_args, **_kwargs: {"id": 7, "custom": object()},
    )

    result = settings_routes.get_proxy_item(7)

    assert result["id"] == 7
    assert isinstance(result["custom"], str)


def test_test_proxy_item_route_handles_runtime_error_without_500(monkeypatch):
    class _Proxy:
        proxy_url = "http://user:pass@p.webshare.io:80"

    def _raise_runtime(*_args, **_kwargs):
        raise RuntimeError("Failed to perform, curl: (56) Proxy CONNECT aborted")

    monkeypatch.setattr(settings_routes, "get_db", _fake_get_db)
    monkeypatch.setattr(settings_routes.crud, "get_proxy_by_id", lambda *_args, **_kwargs: _Proxy())
    monkeypatch.setattr(settings_routes, "_run_proxy_diagnostics", _raise_runtime)
    monkeypatch.setattr(settings_routes, "_resolve_server_public_ip", lambda: "47.129.244.169")

    result = settings_routes.test_proxy_item(1)

    assert result["success"] is False
    assert result["diagnosis_code"] == "proxy_connect_aborted"
    assert "请检查 Webshare 后台是否已授权当前服务器 IP (47.129.244.169)" in result["message"]


def test_resolve_server_public_ip_self_heals_when_import_fails(monkeypatch):
    import builtins

    original_import = builtins.__import__

    def _broken_import(name, *args, **kwargs):
        if name == "requests":
            raise RuntimeError("requests not available")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _broken_import)

    assert settings_routes._resolve_server_public_ip() == ""


def test_diagnose_proxy_failure_407_returns_clear_message():
    result = diagnose_proxy_failure(status_code=407, target_url="https://api.ipify.org?format=json")
    assert result["code"] == "proxy_auth_failed"
    assert "用户名密码" in result["message"]
    assert "欠费" in result["message"]


def test_diagnose_proxy_failure_506_returns_clear_message():
    result = diagnose_proxy_failure(status_code=506, target_url="https://api.ipify.org?format=json")
    assert result["code"] == "proxy_account_blocked"
    assert "欠费" in result["message"]
    assert "加白" in result["message"]
