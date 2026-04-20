from src.web.routes import payment as payment_routes


def test_normalize_third_party_bind_api_url_adds_default_path_and_strips_query(monkeypatch):
    monkeypatch.delenv(payment_routes.ALLOW_UNSAFE_THIRD_PARTY_BIND_URLS_ENV, raising=False)

    normalized = payment_routes._normalize_third_party_bind_api_url(
        "https://vendor.example.com?token=secret#frag"
    )

    assert normalized == "https://vendor.example.com/api/v1/bind-card"


def test_normalize_third_party_bind_api_url_rejects_public_http_by_default(monkeypatch):
    monkeypatch.delenv(payment_routes.ALLOW_UNSAFE_THIRD_PARTY_BIND_URLS_ENV, raising=False)

    normalized = payment_routes._normalize_third_party_bind_api_url(
        "http://vendor.example.com/api/v1/bind-card"
    )

    assert normalized is None


def test_normalize_third_party_bind_api_url_rejects_embedded_credentials(monkeypatch):
    monkeypatch.delenv(payment_routes.ALLOW_UNSAFE_THIRD_PARTY_BIND_URLS_ENV, raising=False)

    normalized = payment_routes._normalize_third_party_bind_api_url(
        "https://user:pass@vendor.example.com/api/v1/bind-card"
    )

    assert normalized is None


def test_normalize_third_party_bind_api_url_rejects_private_hosts_by_default(monkeypatch):
    monkeypatch.delenv(payment_routes.ALLOW_UNSAFE_THIRD_PARTY_BIND_URLS_ENV, raising=False)

    normalized = payment_routes._normalize_third_party_bind_api_url(
        "https://127.0.0.1:8787/api/v1/bind-card"
    )

    assert normalized is None


def test_normalize_third_party_bind_api_url_allows_loopback_with_explicit_opt_in(monkeypatch):
    monkeypatch.setenv(payment_routes.ALLOW_UNSAFE_THIRD_PARTY_BIND_URLS_ENV, "1")

    normalized = payment_routes._normalize_third_party_bind_api_url(
        "http://127.0.0.1:8787"
    )

    assert normalized == "http://127.0.0.1:8787/api/v1/bind-card"
