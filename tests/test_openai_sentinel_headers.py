import asyncio
import json

from src.core import http_client as http_client_module
from src.core.http_client import OpenAIHTTPClient
from src.core.openai import sentinel_headers as headers_module


def test_build_sentinel_token_async_uses_async_pow_and_challenge_solution(monkeypatch):
    calls = []
    solved = {}

    class DummyResponse:
        status_code = 200
        content = b"{}"

        @staticmethod
        def json():
            return {
                "token": "challenge-token",
                "proofofwork": {
                    "required": True,
                    "seed": "seed-1",
                    "difficulty": "ff",
                },
            }

    class DummySession:
        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return DummyResponse()

    async def fake_pow(user_agent, difficulty=headers_module.DEFAULT_SENTINEL_DIFF, max_iterations=headers_module.DEFAULT_MAX_ITERATIONS):
        return "pow-request"

    async def fake_solve(seed, difficulty, config, max_iterations=headers_module.DEFAULT_MAX_ITERATIONS):
        solved.update(
            seed=seed,
            difficulty=difficulty,
            config=config,
            max_iterations=max_iterations,
        )
        return "pow-final"

    monkeypatch.setattr(headers_module, "build_sentinel_pow_token_async", fake_pow)
    monkeypatch.setattr(headers_module, "solve_sentinel_pow_async", fake_solve)
    monkeypatch.setattr(headers_module, "build_sentinel_config", lambda user_agent: ["cfg", user_agent])

    token = asyncio.run(
        headers_module.build_sentinel_token_async(
            DummySession(),
            "did-1",
            flow="password_verify",
            user_agent="ua-1",
        )
    )

    request_body = json.loads(calls[0][1]["data"])
    assert request_body == {"p": "pow-request", "id": "did-1", "flow": "password_verify"}
    assert solved == {
        "seed": "seed-1",
        "difficulty": "ff",
        "config": ["cfg", "ua-1"],
        "max_iterations": headers_module.DEFAULT_MAX_ITERATIONS,
    }
    assert json.loads(token) == {
        "p": f"{headers_module.DEFAULT_SENTINEL_TOKEN_PREFIX}pow-final",
        "t": "",
        "c": "challenge-token",
        "id": "did-1",
        "flow": "password_verify",
    }


def test_build_sentinel_token_sync_bridge_is_safe_under_running_loop(monkeypatch):
    async def fake_build(*args, **kwargs):
        await asyncio.sleep(0)
        return "sentinel-sync"

    monkeypatch.setattr(headers_module, "build_sentinel_token_async", fake_build)

    async def invoke():
        return headers_module.build_sentinel_token(object(), "did-1", flow="authorize_continue")

    assert asyncio.run(invoke()) == "sentinel-sync"


def test_openai_http_client_check_sentinel_async_awaits_async_builder(monkeypatch):
    calls = {}
    client = OpenAIHTTPClient()
    client._session = object()

    async def fake_build(session, did, *, flow, user_agent):
        calls.update(session=session, did=did, flow=flow, user_agent=user_agent)
        return "sentinel-async"

    monkeypatch.setattr(http_client_module, "build_sentinel_token_async", fake_build)

    token = asyncio.run(client.check_sentinel_async("device-1"))

    assert token == "sentinel-async"
    assert calls == {
        "session": client.session,
        "did": "device-1",
        "flow": "authorize_continue",
        "user_agent": client.default_headers["User-Agent"],
    }
