from src.core.anyauto.chatgpt_client import ChatGPTClient
from src.core.anyauto.oauth_client import OAuthClient
from src.core.anyauto.utils import FlowState


class DummyCookie:
    def __init__(self, name, value, domain="chatgpt.com"):
        self.name = name
        self.value = value
        self.domain = domain


class DummyCookies:
    def __init__(self):
        self._cookies = []

    @property
    def jar(self):
        return self._cookies

    def set(self, name, value, domain=None, path="/"):
        domain = domain or ""
        self._cookies = [
            cookie
            for cookie in self._cookies
            if not (cookie.name == name and cookie.domain == domain)
        ]
        self._cookies.append(DummyCookie(name, value, domain=domain))

    def get(self, name, default=None):
        for cookie in reversed(self._cookies):
            if cookie.name == name:
                return cookie.value
        return default


class DummyResponse:
    def __init__(self, url, status_code=200, headers=None, history=None, payload=None):
        self.url = url
        self.status_code = status_code
        self.headers = headers or {}
        self.history = history or []
        self._payload = payload
        self.text = ""

    def json(self):
        if self._payload is None:
            raise ValueError("no payload")
        return self._payload


class DummySession:
    def __init__(self, response=None):
        self.cookies = DummyCookies()
        self.headers = {}
        self._response = response or DummyResponse("https://auth.openai.com/log-in")
        self.posts = []

    def get(self, url, **kwargs):
        return self._response

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return self._response


def _build_chatgpt_client():
    client = ChatGPTClient.__new__(ChatGPTClient)
    client.proxy = None
    client.verbose = False
    client.browser_mode = "protocol"
    client.device_id = "did-1"
    client.refresh_token = ""
    client.last_code_verifier = None
    client.last_oauth_client_id = None
    client.last_oauth_redirect_uri = None
    client.last_oauth_state = None
    client.last_follow_callback_url = ""
    client.last_follow_final_url = ""
    client.last_registration_state = FlowState()
    client.ua = "Mozilla/5.0"
    client.sec_ch_ua = '"Chromium";v="136"'
    client.impersonate = "chrome136"
    client.session = DummySession()
    client._browser_pause = lambda *args, **kwargs: None
    client._headers = lambda *args, **kwargs: {}
    client._log = lambda *args, **kwargs: None
    return client


def test_authorize_syncs_nextauth_pkce_cookie():
    client = _build_chatgpt_client()

    auth_url = (
        "https://auth.openai.com/oauth/authorize"
        "?client_id=app_test"
        "&redirect_uri=https%3A%2F%2Fchatgpt.com%2Fapi%2Fauth%2Fcallback%2Fopenai"
        "&state=state-1"
    )

    final_url = client.authorize(auth_url)

    assert final_url == "https://auth.openai.com/log-in"
    assert client.last_oauth_client_id == "app_test"
    assert client.last_oauth_state == "state-1"
    assert client.last_code_verifier
    assert (
        client.session.cookies.get("__Secure-next-auth.pkce.code_verifier")
        == client.last_code_verifier
    )


def test_login_passwordless_uses_passwordless_send_otp(monkeypatch):
    client = OAuthClient(
        config={
            "oauth_issuer": "https://auth.openai.com",
            "oauth_client_id": "app_test",
            "oauth_redirect_uri": "http://localhost:1455/auth/callback",
        },
        verbose=False,
    )
    client._log = lambda *args, **kwargs: None

    calls = []

    monkeypatch.setattr(
        client,
        "_bootstrap_oauth_session",
        lambda *args, **kwargs: "https://auth.openai.com/log-in",
    )
    monkeypatch.setattr(
        client,
        "_submit_authorize_continue",
        lambda *args, **kwargs: FlowState(
            page_type="login_password",
            current_url="https://auth.openai.com/log-in/password",
        ),
    )

    def fake_send_passwordless_otp(device_id, user_agent, sec_ch_ua, impersonate, referer=None):
        calls.append(("passwordless", referer))
        return True, FlowState(
            page_type="email_otp_verification",
            current_url="https://auth.openai.com/email-verification",
        ), ""

    monkeypatch.setattr(client, "_send_passwordless_otp", fake_send_passwordless_otp)
    monkeypatch.setattr(
        client,
        "_send_email_otp",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not call email-otp/send")),
    )
    monkeypatch.setattr(
        client,
        "_handle_otp_verification",
        lambda *args, **kwargs: FlowState(
            page_type="oauth_callback",
            current_url="http://localhost:1455/auth/callback?code=code-1&state=state-1",
        ),
    )
    monkeypatch.setattr(
        client,
        "_exchange_code_for_tokens",
        lambda *args, **kwargs: {
            "access_token": "at-1",
            "refresh_token": "oaistb_rt_test_1",
            "id_token": "id-1",
        },
    )

    result = client.login_passwordless_and_get_tokens(
        "tester@example.com",
        "did-1",
        "Mozilla/5.0",
        '"Chromium";v="136"',
        "chrome136",
        skymail_client=object(),
    )

    assert result["refresh_token"] == "oaistb_rt_test_1"
    assert calls == [("passwordless", "https://auth.openai.com/log-in/password")]


def test_oauth_client_apply_auth_context_reuses_session():
    source_client = _build_chatgpt_client()
    source_client.session.cookies.set("login_session", "login-1", domain="auth.openai.com")

    client = OAuthClient(
        config={
            "oauth_issuer": "https://auth.openai.com",
            "oauth_client_id": "app_test",
            "oauth_redirect_uri": "http://localhost:1455/auth/callback",
        },
        verbose=False,
    )

    client.apply_auth_context(source_client.export_auth_context())

    assert client.session is source_client.session
    assert client.session.cookies.get("login_session") == "login-1"


def test_authorize_continue_retries_invalid_state(monkeypatch):
    client = OAuthClient(
        config={
            "oauth_issuer": "https://auth.openai.com",
            "oauth_client_id": "app_test",
            "oauth_redirect_uri": "http://localhost:1455/auth/callback",
        },
        verbose=False,
    )
    client._log = lambda *args, **kwargs: None
    client._browser_pause = lambda *args, **kwargs: None
    client._headers = lambda *args, **kwargs: {}

    class SequenceSession:
        def __init__(self):
            self.headers = {}
            self.cookies = DummyCookies()
            self.calls = 0

        def post(self, url, **kwargs):
            self.calls += 1
            if self.calls == 1:
                response = DummyResponse(url, status_code=409)
                response.text = '{"error":{"code":"invalid_state"}}'
                return response
            return DummyResponse(
                url,
                status_code=200,
                headers={"content-type": "application/json"},
                payload={
                    "page": {"type": "login_password"},
                    "continue_url": "https://auth.openai.com/log-in/password",
                    "method": "GET",
                },
            )

    session = SequenceSession()
    client.session = session

    bootstrap_calls = []

    monkeypatch.setattr("src.core.anyauto.oauth_client.build_sentinel_token", lambda *args, **kwargs: "sentinel-1")
    monkeypatch.setattr(
        client,
        "_bootstrap_oauth_session",
        lambda *args, **kwargs: bootstrap_calls.append(1) or "https://auth.openai.com/log-in",
    )

    state = client._submit_authorize_continue(
        "tester@example.com",
        "did-1",
        "https://auth.openai.com/log-in",
        authorize_url="https://auth.openai.com/oauth/authorize",
        authorize_params={"state": "state-1"},
    )

    assert state is not None
    assert state.page_type == "login_password"
    assert session.calls == 2
    assert bootstrap_calls == [1]


def test_passwordless_otp_prefers_passwordless_verify_endpoint():
    client = OAuthClient(
        config={
            "oauth_issuer": "https://auth.openai.com",
            "oauth_client_id": "app_test",
            "oauth_redirect_uri": "http://localhost:1455/auth/callback",
        },
        verbose=False,
    )
    client._log = lambda *args, **kwargs: None
    client._browser_pause = lambda *args, **kwargs: None
    client._headers = lambda *args, **kwargs: {}

    class OTPResponseSession:
        def __init__(self):
            self.headers = {}
            self.cookies = DummyCookies()
            self.urls = []

        def post(self, url, **kwargs):
            self.urls.append(url)
            return DummyResponse(
                url,
                status_code=200,
                headers={"content-type": "application/json"},
                payload={
                    "page": {"type": "oauth_callback"},
                    "continue_url": "http://localhost:1455/auth/callback?code=code-1&state=state-1",
                    "method": "GET",
                },
            )

    client.session = OTPResponseSession()

    class Mailbox:
        def __init__(self):
            self._used_codes = set()

        def wait_for_verification_code(self, *_args, **_kwargs):
            return "123456"

    next_state = client._handle_otp_verification(
        "tester@example.com",
        "did-1",
        "Mozilla/5.0",
        '"Chromium";v="136"',
        "chrome136",
        Mailbox(),
        FlowState(
            page_type="email_otp_verification",
            current_url="https://auth.openai.com/email-verification",
        ),
        passwordless=True,
    )

    assert next_state is not None
    assert client.session.urls[0].endswith("/api/accounts/passwordless/verify-otp")


def test_reuse_session_uses_captured_callback_for_manual_exchange(monkeypatch):
    client = _build_chatgpt_client()
    client.last_registration_state = FlowState(
        page_type="external_url",
        continue_url="https://auth.openai.com/continue",
        current_url="https://auth.openai.com/continue",
    )
    client.last_code_verifier = "verifier-1"
    client.last_oauth_client_id = "app_test"
    client.last_oauth_redirect_uri = "https://chatgpt.com/api/auth/callback/openai"

    events = []

    def fake_follow(state, referer=None, stop_before_callback=False, max_hops=16):
        events.append(("follow", stop_before_callback))
        client.last_follow_callback_url = (
            "https://chatgpt.com/api/auth/callback/openai?code=code-1&state=state-1"
        )
        client.last_follow_final_url = "https://chatgpt.com/auth/error?error=OAuthCallback"
        return True, FlowState(
            page_type="auth_error",
            current_url="https://chatgpt.com/auth/error?error=OAuthCallback",
        )

    client._follow_flow_state = fake_follow
    finalized = []
    client._finalize_nextauth_callback = lambda callback_url, referer=None: events.append(("finalize", callback_url, referer)) or finalized.append((callback_url, referer)) or False
    client.get_next_auth_session_token = lambda: ""

    class FakeOAuthClient:
        def __init__(self, *args, **kwargs):
            self.session = None

        def apply_auth_context(self, context):
            self.session = context.get("session")
            return self.session

        def _exchange_code_for_tokens(self, code, code_verifier, user_agent, impersonate):
            events.append(("exchange", code, code_verifier))
            assert code == "code-1"
            assert code_verifier == "verifier-1"
            return {
                "access_token": "at-1",
                "refresh_token": "oaistb_rt_manual_1",
                "id_token": "id-1",
            }

    monkeypatch.setattr("src.core.anyauto.oauth_client.OAuthClient", FakeOAuthClient)

    success, data = client.reuse_session_and_get_tokens()

    assert success is True
    assert data["refresh_token"] == "oaistb_rt_manual_1"
    assert data["auth_provider"] == "oauth_token_exchange"
    assert finalized == [("https://chatgpt.com/api/auth/callback/openai?code=code-1&state=state-1", "https://chatgpt.com/auth/login")]
    assert events == [
        ("follow", True),
        ("exchange", "code-1", "verifier-1"),
        (
            "finalize",
            "https://chatgpt.com/api/auth/callback/openai?code=code-1&state=state-1",
            "https://chatgpt.com/auth/login",
        ),
    ]


def test_follow_flow_state_stops_before_oauth_callback():
    client = _build_chatgpt_client()
    requests = []

    class RedirectSession:
        def __init__(self):
            self.cookies = DummyCookies()
            self.headers = {}

        def get(self, url, **kwargs):
            requests.append((url, kwargs.get("allow_redirects")))
            if url == "https://auth.openai.com/continue":
                return DummyResponse(
                    url,
                    status_code=302,
                    headers={
                        "Location": "https://chatgpt.com/api/auth/callback/openai?code=code-1&state=state-1"
                    },
                )
            raise AssertionError(f"unexpected GET {url}")

    client.session = RedirectSession()

    ok, state = client._follow_flow_state(
        FlowState(
            page_type="external_url",
            continue_url="https://auth.openai.com/continue",
            current_url="https://auth.openai.com/continue",
        ),
        referer="https://auth.openai.com/about-you",
        stop_before_callback=True,
    )

    assert ok is True
    assert state.current_url == "https://chatgpt.com/api/auth/callback/openai?code=code-1&state=state-1"
    assert client.last_follow_callback_url == state.current_url
    assert requests == [("https://auth.openai.com/continue", False)]
