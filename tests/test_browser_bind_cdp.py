import io

from src.core.openai import browser_bind


class _FakeStderr:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self._fd = 123

    def fileno(self):
        return self._fd

    def read_chunk(self):
        if not self._chunks:
            return b""
        return self._chunks.pop(0).encode("utf-8")


class _FakeProc:
    def __init__(self, chunks):
        self.stderr = _FakeStderr(chunks)

    def poll(self):
        return None


def test_wait_for_cdp_ready_accepts_devtools_stderr(monkeypatch):
    proc = _FakeProc(["DevTools listening on ws://127.0.0.1:9666/devtools/browser/test\n"])

    monkeypatch.setattr(browser_bind.os, "set_blocking", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(browser_bind.os, "read", lambda fd, _size: proc.stderr.read_chunk() if fd == 123 else b"")
    monkeypatch.setattr(
        browser_bind.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionRefusedError("refused")),
    )

    ready, stderr_text = browser_bind.wait_for_cdp_ready("http://127.0.0.1:9666", proc, timeout_seconds=1)

    assert ready is True
    assert "DevTools listening on" in stderr_text


def test_wait_for_cdp_ready_accepts_json_endpoint(monkeypatch):
    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"Browser":"Chrome/139.0"}'

    proc = _FakeProc([""])

    monkeypatch.setattr(browser_bind.os, "set_blocking", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(browser_bind.os, "read", lambda _fd, _size: b"")
    monkeypatch.setattr(browser_bind.urllib.request, "urlopen", lambda *_args, **_kwargs: _Response())

    ready, stderr_text = browser_bind.wait_for_cdp_ready("http://127.0.0.1:9666", proc, timeout_seconds=1)

    assert ready is True
    assert stderr_text == ""
