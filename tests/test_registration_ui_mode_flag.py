from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_index_template_contains_refresh_token_switch():
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8-sig")

    assert 'id="refresh-token-enabled"' in html
    assert 'name="refresh_token_enabled"' in html


def test_app_js_includes_refresh_token_enabled_in_request_payload():
    script = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8-sig")

    assert "refresh_token_enabled:" in script
    assert "elements.refreshTokenEnabled" in script
