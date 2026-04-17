import asyncio
import base64

from src.core.openai import sentinel as sentinel_module


def test_format_browser_time_uses_dynamic_timezone_override(monkeypatch):
    monkeypatch.setenv("OPENAI_SENTINEL_TIMEZONE", "+0530")
    monkeypatch.setenv("OPENAI_SENTINEL_TIMEZONE_LABEL", "India Standard Time")

    formatted = sentinel_module._format_browser_time()

    assert "GMT+0530 (India Standard Time)" in formatted


def test_build_sentinel_config_uses_dynamic_locale_defaults(monkeypatch):
    profile = sentinel_module.BrowserFingerprintProfile(4000, 4294705152, 8, "location", "documentElement", "window")
    monkeypatch.setenv("OPENAI_SENTINEL_LOCALE", "zh_CN.UTF-8")
    monkeypatch.delenv("OPENAI_SENTINEL_LANGUAGES", raising=False)
    monkeypatch.setattr(sentinel_module, "_choose_browser_profile", lambda user_agent: profile)

    config = sentinel_module.build_sentinel_config("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")

    assert config[0] == 4000
    assert config[2] == 4294705152
    assert config[7] == "zh-CN"
    assert config[8] == "zh-CN,zh,en-US,en"
    assert config[10:13] == ["location", "documentElement", "window"]


def test_build_sentinel_pow_token_uses_configurable_prefix(monkeypatch):
    monkeypatch.setenv("OPENAI_SENTINEL_TOKEN_PREFIX", "prefix-")
    monkeypatch.setattr(sentinel_module, "build_sentinel_config", lambda user_agent: ["config"])
    monkeypatch.setattr(sentinel_module, "_generate_pow_seed", lambda: "seed-1")

    captured = {}

    def fake_solve(seed, difficulty, config, max_iterations):
        captured.update(
            seed=seed,
            difficulty=difficulty,
            config=config,
            max_iterations=max_iterations,
        )
        return "payload"

    monkeypatch.setattr(sentinel_module, "solve_sentinel_pow", fake_solve)

    token = sentinel_module.build_sentinel_pow_token("ua", difficulty="ff", max_iterations=7)

    assert token == "prefix-payload"
    assert captured == {
        "seed": "seed-1",
        "difficulty": "ff",
        "config": ["config"],
        "max_iterations": 7,
    }


def test_build_sentinel_pow_token_async_uses_to_thread(monkeypatch):
    calls = {}

    async def fake_to_thread(func, *args, **kwargs):
        calls["func"] = func
        calls["args"] = args
        calls["kwargs"] = kwargs
        return "async-token"

    monkeypatch.setattr(sentinel_module.asyncio, "to_thread", fake_to_thread)

    token = asyncio.run(
        sentinel_module.build_sentinel_pow_token_async("ua", difficulty="0f", max_iterations=9)
    )

    assert token == "async-token"
    assert calls["func"] is sentinel_module.build_sentinel_pow_token
    assert calls["args"] == ()
    assert calls["kwargs"] == {
        "user_agent": "ua",
        "difficulty": "0f",
        "max_iterations": 9,
    }


def test_solve_sentinel_pow_returns_base64_payload():
    config = [
        4000,
        "Mon Apr 13 2026 10:00:00 GMT+0800 (China Standard Time)",
        4294705152,
        0,
        "ua",
        "",
        "",
        "en-US",
        "en-US,en",
        0,
        "location",
        "documentElement",
        "window",
        1.0,
        "sid",
        "",
        8,
        2.0,
    ]

    encoded = sentinel_module.solve_sentinel_pow("seed", "ff", config, max_iterations=1)

    assert isinstance(encoded, str)
    assert base64.b64decode(encoded)
