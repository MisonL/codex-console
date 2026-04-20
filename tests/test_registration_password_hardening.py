import base64
import json

from src.core import register as register_module
from src.core.anyauto import chatgpt_client as chatgpt_client_module
from src.core.anyauto import register_flow as register_flow_module
from src.core.anyauto.chatgpt_client import ChatGPTClient
from src.core.anyauto.register_flow import AnyAutoRegistrationEngine
from src.core.anyauto.utils import FlowState
from src.core.openai.sentinel_browser import BrowserSentinelArtifacts
from src.core.register import RegistrationEngine
from src.core.utils import generate_password


def _assert_password_is_hardened(password: str) -> None:
    assert len(password) >= 8
    assert any(ch.islower() for ch in password)
    assert any(ch.isupper() for ch in password)
    assert any(ch.isdigit() for ch in password)
    assert any(not ch.isalnum() for ch in password)


def test_generate_password_contains_special_characters():
    _assert_password_is_hardened(generate_password(12))


def test_registration_engine_generate_password_contains_special_characters():
    engine = RegistrationEngine.__new__(RegistrationEngine)
    _assert_password_is_hardened(RegistrationEngine._generate_password(engine, 12))


def test_anyauto_generate_password_contains_special_characters():
    _assert_password_is_hardened(AnyAutoRegistrationEngine._build_password(12))


def test_register_password_with_retry_retries_generic_400(monkeypatch):
    engine = RegistrationEngine.__new__(RegistrationEngine)
    attempts = []
    logs = []

    def fake_register_password(_did=None, _sen_token=None):
        attempts.append(1)
        if len(attempts) < 3:
            engine._last_register_password_error = "注册密码接口返回异常: Failed to create account. Please try again."
            return False, None
        return True, "Aa1!retryPwd"

    monkeypatch.setattr(register_module.time, "sleep", lambda _seconds: None)
    engine._register_password = fake_register_password
    engine._last_register_password_error = None
    engine._log = lambda message, level="info": logs.append((level, message))

    success, password = RegistrationEngine._register_password_with_retry(engine, None, None)

    assert success is True
    assert password == "Aa1!retryPwd"
    assert len(attempts) == 3
    assert any("可重试 400" in message for _level, message in logs)


def test_register_password_with_retry_does_not_retry_registration_disallowed(monkeypatch):
    engine = RegistrationEngine.__new__(RegistrationEngine)
    attempts = []
    logs = []

    def fake_register_password(_did=None, _sen_token=None):
        attempts.append(1)
        engine._last_register_password_error = (
            "注册密码接口返回异常: Sorry, we cannot create your account with the given information. "
            "(registration_disallowed)"
        )
        return False, None

    monkeypatch.setattr(register_module.time, "sleep", lambda _seconds: None)
    engine._register_password = fake_register_password
    engine._last_register_password_error = None
    engine._log = lambda message, level="info": logs.append((level, message))

    success, password = RegistrationEngine._register_password_with_retry(engine, "did-1", "sentinel-1")

    assert success is False
    assert password is None
    assert len(attempts) == 1
    assert not any("可重试 400" in message for _level, message in logs)


def test_register_password_with_retry_upgrades_repeated_generic_400_to_environment_rejection(monkeypatch):
    engine = RegistrationEngine.__new__(RegistrationEngine)
    attempts = []
    logs = []
    refreshed_tokens = []

    def fake_register_password(_did=None, _sen_token=None):
        attempts.append(1)
        engine._last_register_password_error = "注册密码接口返回异常: Failed to create account. Please try again."
        engine._last_register_password_request_id = f"req-{len(attempts)}"
        return False, None

    monkeypatch.setattr(register_module.time, "sleep", lambda _seconds: None)
    engine._register_password = fake_register_password
    engine._check_sentinel = lambda did: refreshed_tokens.append(did) or f"sentinel-{len(refreshed_tokens)}"
    engine._last_register_password_error = None
    engine._last_register_password_request_id = None
    engine._log = lambda message, level="info": logs.append((level, message))

    success, password = RegistrationEngine._register_password_with_retry(engine, "did-1", "sentinel-1")

    assert success is False
    assert password is None
    assert len(attempts) == 3
    assert refreshed_tokens == ["did-1", "did-1"]
    assert "当前出口 IP / 设备指纹 / 会话环境很可能触发风控" in engine._last_register_password_error
    assert "x-request-id: req-3" in engine._last_register_password_error
    assert any("连续拒绝当前注册请求" in message for _level, message in logs)


def test_anyauto_should_not_retry_registration_disallowed():
    assert register_flow_module.AnyAutoRegistrationEngine._should_retry(
        "创建账号失败: HTTP 400 registration_disallowed"
    ) is False


def test_anyauto_should_not_retry_environment_rejection_diagnostic():
    assert register_flow_module.AnyAutoRegistrationEngine._should_retry(
        "OpenAI 在 create-account/password 阶段直接拒绝当前注册请求，当前出口 IP / 设备指纹 / 会话环境很可能触发风控（x-request-id: req-9）"
    ) is False


def test_anyauto_capture_partial_auth_result_preserves_phone_required_context():
    def _encode_cookie(payload):
        return (
            base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8"))
            .decode("ascii")
            .rstrip("=")
        )

    def _make_jwt(payload):
        header = _encode_cookie({"alg": "none", "typ": "JWT"})
        body = _encode_cookie(payload)
        return f"{header}.{body}.sig"

    class _Cookies:
        def __init__(self, values):
            self._values = dict(values)

        def get(self, key):
            return self._values.get(key)

    class _Session:
        def __init__(self):
            self.cookies = _Cookies(
                {
                    "oai-client-auth-session": _encode_cookie(
                        {"workspaces": [{"id": "ws-1"}]}
                    ),
                }
            )

    class _ChatGPTClient:
        def __init__(self):
            self.session = _Session()
            self.refresh_token = ""

        def get_next_auth_session_token(self):
            return "session-1"

        def fetch_chatgpt_session(self):
            return True, {
                "accessToken": _make_jwt(
                    {
                        "https://api.openai.com/auth": {
                            "chatgpt_account_id": "acct-1",
                            "chatgpt_user_id": "user-1",
                        }
                    }
                ),
                "sessionToken": "session-1",
                "user": {"id": "user-1"},
                "account": {"id": "acct-1"},
                "authProvider": "openai",
                "expires": "2026-04-09T10:00:00Z",
            }

    engine = AnyAutoRegistrationEngine.__new__(AnyAutoRegistrationEngine)
    engine._log = lambda *_args, **_kwargs: None

    partial = AnyAutoRegistrationEngine._capture_partial_auth_result(
        engine,
        _ChatGPTClient(),
    )

    assert partial["session_token"] == "session-1"
    assert partial["account_id"] == "acct-1"
    assert partial["workspace_id"] == "ws-1"
    assert partial["metadata"]["user_id"] == "user-1"
    assert partial["metadata"]["auth_provider"] == "openai"


def test_anyauto_register_user_sends_device_and_sentinel_headers(monkeypatch):
    calls = []

    class DummySession:
        def post(self, url, **kwargs):
            calls.append((url, kwargs))

            class Response:
                url = "https://auth.openai.com/api/accounts/user/register"
                status_code = 200
                headers = {}
                text = "{}"

                @staticmethod
                def json():
                    return {}

            return Response()

    client = ChatGPTClient.__new__(ChatGPTClient)
    client.AUTH = "https://auth.openai.com"
    client.session = DummySession()
    client.proxy = None
    client.device_id = "did-456"
    client.ua = "ua"
    client.sec_ch_ua = '"Chromium";v="145"'
    client.impersonate = "chrome"
    client.browser_mode = "protocol"
    client.last_registration_state = FlowState()
    client._log = lambda *_args, **_kwargs: None
    client._browser_pause = lambda *_args, **_kwargs: None
    client._headers = lambda url, **kwargs: {"accept": kwargs["accept"], **(kwargs.get("extra_headers") or {})}

    monkeypatch.setattr(
        client,
        "_fetch_browser_sentinel_artifacts",
        lambda **kwargs: BrowserSentinelArtifacts(
            token='{"id":"did-456","flow":"username_password_create","c":"sentinel"}',
            passkey_capabilities='{"conditionalGet":true}',
        ),
    )
    monkeypatch.setattr(chatgpt_client_module, "build_sentinel_token", lambda *args, **kwargs: "")
    monkeypatch.setattr(chatgpt_client_module, "generate_datadog_trace", lambda: {"x-trace-id": "trace-1"})

    success, message = ChatGPTClient.register_user(client, "tester@example.com", "Aa1!fixedPwd")

    assert success is True
    assert message == "注册成功"
    assert len(calls) == 1
    url, kwargs = calls[0]
    assert url == "https://auth.openai.com/api/accounts/user/register"
    assert kwargs["json"] == {"username": "tester@example.com", "password": "Aa1!fixedPwd"}
    assert kwargs["headers"]["oai-device-id"] == "did-456"
    assert kwargs["headers"]["openai-sentinel-token"] == '{"id":"did-456","flow":"username_password_create","c":"sentinel"}'
    assert "OpenAI-Sentinel-Token" not in kwargs["headers"]
    assert "OpenAI-Device-Id" not in kwargs["headers"]
    assert "ext-passkey-client-capabilities" not in kwargs["headers"]
    assert kwargs["headers"]["x-trace-id"] == "trace-1"


def test_anyauto_register_user_prefers_browser_sentinel_before_pow(monkeypatch):
    calls = []
    pow_flows = []

    class DummySession:
        def post(self, url, **kwargs):
            calls.append((url, kwargs))

            class Response:
                url = "https://auth.openai.com/api/accounts/user/register"
                status_code = 200
                headers = {}
                text = "{}"

                @staticmethod
                def json():
                    return {}

            return Response()

    client = ChatGPTClient.__new__(ChatGPTClient)
    client.AUTH = "https://auth.openai.com"
    client.session = DummySession()
    client.proxy = None
    client.device_id = "did-456"
    client.ua = "ua"
    client.sec_ch_ua = '"Chromium";v="145"'
    client.impersonate = "chrome"
    client.browser_mode = "protocol"
    client.last_registration_state = FlowState()
    client._log = lambda *_args, **_kwargs: None
    client._browser_pause = lambda *_args, **_kwargs: None
    client._headers = lambda url, **kwargs: {"accept": kwargs["accept"], **(kwargs.get("extra_headers") or {})}

    def fake_browser(**kwargs):
        return BrowserSentinelArtifacts(
            token='{"id":"did-456","flow":"username_password_create","c":"browser"}',
            passkey_capabilities='{"conditionalGet":true}',
        )

    def fake_pow(*args, **kwargs):
        flow = kwargs.get("flow")
        pow_flows.append(flow)
        if flow == "authorize_continue":
            return "authorize-continue-token"
        return "pow-should-not-run"

    monkeypatch.setattr(client, "_fetch_browser_sentinel_artifacts", fake_browser)
    monkeypatch.setattr(chatgpt_client_module, "build_sentinel_token", fake_pow)
    monkeypatch.setattr(chatgpt_client_module, "generate_datadog_trace", lambda: {"x-trace-id": "trace-1"})

    success, message = ChatGPTClient.register_user(client, "tester@example.com", "Aa1!fixedPwd")

    assert success is True
    assert message == "注册成功"
    assert pow_flows == []
    assert calls[0][1]["headers"]["openai-sentinel-token"] == '{"id":"did-456","flow":"username_password_create","c":"browser"}'


def test_anyauto_create_account_falls_back_to_pow_when_browser_sentinel_missing(monkeypatch):
    calls = []
    sequence = []

    class DummySession:
        def post(self, url, **kwargs):
            calls.append((url, kwargs))

            class Response:
                url = "https://auth.openai.com/api/accounts/create_account"
                status_code = 200
                headers = {}
                text = "{}"

                @staticmethod
                def json():
                    return {}

            return Response()

    client = ChatGPTClient.__new__(ChatGPTClient)
    client.AUTH = "https://auth.openai.com"
    client.BASE = "https://chatgpt.com"
    client.session = DummySession()
    client.proxy = None
    client.device_id = "did-456"
    client.refresh_token = ""
    client.ua = "ua"
    client.sec_ch_ua = '"Chromium";v="145"'
    client.impersonate = "chrome"
    client.browser_mode = "protocol"
    client._log = lambda *_args, **_kwargs: None
    client._browser_pause = lambda *_args, **_kwargs: None
    client._headers = lambda url, **kwargs: {"accept": kwargs["accept"], **(kwargs.get("extra_headers") or {})}
    client._sniff_refresh_token = lambda *_args, **_kwargs: None
    client._sync_response_cookies = lambda *_args, **_kwargs: None
    client._state_from_payload = lambda *_args, **_kwargs: FlowState(page_type="chatgpt_home")

    def fake_browser(**kwargs):
        sequence.append(("browser", kwargs["flow"]))
        return None

    def fake_pow(*args, **kwargs):
        sequence.append(("pow", kwargs.get("flow")))
        return "pow-token"

    monkeypatch.setattr(client, "_fetch_browser_sentinel_artifacts", fake_browser)
    monkeypatch.setattr(chatgpt_client_module, "build_sentinel_token", fake_pow)
    monkeypatch.setattr(chatgpt_client_module, "generate_datadog_trace", lambda: {"x-trace-id": "trace-1"})

    success, message = ChatGPTClient.create_account(
        client,
        "Olivia",
        "Johnson",
        "2005-08-01",
    )

    assert success is True
    assert message == "账号创建成功"
    assert sequence == [("browser", "oauth_create_account"), ("pow", "oauth_create_account")]
    assert calls[0][1]["headers"]["OpenAI-Sentinel-Token"] == "pow-token"


def test_anyauto_register_user_preserves_empty_json_sentinel_when_all_strategies_fail(monkeypatch):
    calls = []

    class DummySession:
        def post(self, url, **kwargs):
            calls.append((url, kwargs))

            class Response:
                url = "https://auth.openai.com/api/accounts/user/register"
                status_code = 200
                headers = {}
                text = "{}"

                @staticmethod
                def json():
                    return {}

            return Response()

    client = ChatGPTClient.__new__(ChatGPTClient)
    client.AUTH = "https://auth.openai.com"
    client.session = DummySession()
    client.proxy = None
    client.device_id = "did-456"
    client.ua = "ua"
    client.sec_ch_ua = '"Chromium";v="145"'
    client.impersonate = "chrome"
    client.browser_mode = "protocol"
    client.last_registration_state = FlowState()
    client._log = lambda *_args, **_kwargs: None
    client._browser_pause = lambda *_args, **_kwargs: None
    client._headers = lambda url, **kwargs: {"accept": kwargs["accept"], **(kwargs.get("extra_headers") or {})}

    monkeypatch.setattr(client, "_fetch_browser_sentinel_artifacts", lambda **kwargs: None)
    monkeypatch.setattr(chatgpt_client_module, "build_sentinel_token", lambda *args, **kwargs: "")
    monkeypatch.setattr(chatgpt_client_module, "generate_datadog_trace", lambda: {"x-trace-id": "trace-1"})

    success, message = ChatGPTClient.register_user(client, "tester@example.com", "Aa1!fixedPwd")

    assert success is True
    assert message == "注册成功"
    assert calls[0][1]["headers"]["openai-sentinel-token"] == "{}"


def test_anyauto_create_account_preserves_empty_json_sentinel_when_all_strategies_fail(monkeypatch):
    calls = []

    class DummySession:
        def post(self, url, **kwargs):
            calls.append((url, kwargs))

            class Response:
                url = "https://auth.openai.com/api/accounts/create_account"
                status_code = 200
                headers = {}
                text = "{}"

                @staticmethod
                def json():
                    return {}

            return Response()

    client = ChatGPTClient.__new__(ChatGPTClient)
    client.AUTH = "https://auth.openai.com"
    client.BASE = "https://chatgpt.com"
    client.session = DummySession()
    client.proxy = None
    client.device_id = "did-456"
    client.refresh_token = ""
    client.ua = "ua"
    client.sec_ch_ua = '"Chromium";v="145"'
    client.impersonate = "chrome"
    client.browser_mode = "protocol"
    client._log = lambda *_args, **_kwargs: None
    client._browser_pause = lambda *_args, **_kwargs: None
    client._headers = lambda url, **kwargs: {"accept": kwargs["accept"], **(kwargs.get("extra_headers") or {})}
    client._sniff_refresh_token = lambda *_args, **_kwargs: None
    client._sync_response_cookies = lambda *_args, **_kwargs: None
    client._state_from_payload = lambda *_args, **_kwargs: FlowState(page_type="chatgpt_home")

    monkeypatch.setattr(client, "_fetch_browser_sentinel_artifacts", lambda **kwargs: None)
    monkeypatch.setattr(chatgpt_client_module, "build_sentinel_token", lambda *args, **kwargs: "")
    monkeypatch.setattr(chatgpt_client_module, "generate_datadog_trace", lambda: {"x-trace-id": "trace-1"})

    success, message = ChatGPTClient.create_account(
        client,
        "Olivia",
        "Johnson",
        "2005-08-01",
    )

    assert success is True
    assert message == "账号创建成功"
    assert calls[0][1]["headers"]["OpenAI-Sentinel-Token"] == "{}"


def test_anyauto_register_user_upgrades_generic_400_to_environment_rejection(monkeypatch):
    class DummySession:
        def post(self, url, **kwargs):
            class Response:
                url = "https://auth.openai.com/api/accounts/user/register"
                status_code = 400
                headers = {"x-request-id": "req-400"}
                text = '{"error":{"message":"Failed to create account. Please try again.","type":"invalid_request_error","code":null}}'

                @staticmethod
                def json():
                    return {
                        "error": {
                            "message": "Failed to create account. Please try again.",
                            "type": "invalid_request_error",
                            "code": None,
                        }
                    }

            return Response()

    client = ChatGPTClient.__new__(ChatGPTClient)
    client.AUTH = "https://auth.openai.com"
    client.session = DummySession()
    client.proxy = None
    client.device_id = "did-456"
    client.ua = "ua"
    client.sec_ch_ua = '"Chromium";v="145"'
    client.impersonate = "chrome"
    client.browser_mode = "protocol"
    client.last_registration_state = FlowState()
    client._log = lambda *_args, **_kwargs: None
    client._browser_pause = lambda *_args, **_kwargs: None
    client._headers = lambda url, **kwargs: {"accept": kwargs["accept"], **(kwargs.get("extra_headers") or {})}

    monkeypatch.setattr(
        client,
        "_fetch_browser_sentinel_artifacts",
        lambda **kwargs: BrowserSentinelArtifacts(
            token='{"id":"did-456","flow":"username_password_create","c":"sentinel"}',
            passkey_capabilities='{"conditionalGet":true}',
        ),
    )
    monkeypatch.setattr(chatgpt_client_module, "build_sentinel_token", lambda *args, **kwargs: "")
    monkeypatch.setattr(chatgpt_client_module, "generate_datadog_trace", lambda: {"x-trace-id": "trace-1"})

    success, message = ChatGPTClient.register_user(client, "tester@example.com", "Aa1!fixedPwd")

    assert success is False
    assert "当前出口 IP / 设备指纹 / 会话环境很可能触发风控" in message
    assert "x-request-id: req-400" in message
