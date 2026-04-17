import io
import tempfile
import unittest
from pathlib import Path

import get_bad_accounts


class FakeHeaders:
    def __init__(self, charset: str = "utf-8"):
        self._charset = charset

    def get_content_charset(self):
        return self._charset


class FakeHttpResponse:
    def __init__(self, payload: bytes, status: int = 200):
        self._buffer = io.BytesIO(payload)
        self.status = status
        self.headers = FakeHeaders()

    def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None


class GetBadAccountsTests(unittest.TestCase):
    def test_write_bad_accounts_supports_top_level_files_object(self):
        payload = (
            b'{"page":1,"files":['
            b'{"name":"good.json","status":"active","status_message":"ok"},'
            b'{"name":"expired.json","status":"expired"},'
            b'{"filename":"error.json","status":"error"},'
            b'{"file_name":"reused.json","status":"active","status_message":"token reused elsewhere"}'
            b']}'
        )
        output = io.StringIO()

        count = get_bad_accounts.write_bad_accounts(
            io.BytesIO(payload),
            output,
            chunk_size=7,
            encoding="utf-8",
        )

        self.assertEqual(count, 3)
        self.assertEqual(
            output.getvalue().splitlines(),
            ["expired.json", "error.json", "reused.json"],
        )

    def test_write_bad_accounts_supports_top_level_array(self):
        payload = (
            b'['
            b'{"name":"one.json","status":"EXPIRED"},'
            b'{"name":"two.json","status":"active","status_message":"already ReUsEd"}'
            b']'
        )

        names = list(
            get_bad_accounts.iter_bad_account_names(
                io.BytesIO(payload),
                chunk_size=5,
                encoding="utf-8",
            )
        )

        self.assertEqual(names, ["one.json", "two.json"])

    def test_write_bad_accounts_raises_when_bad_item_has_no_filename(self):
        payload = b'[{"status":"error","status_message":"broken"}]'

        with self.assertRaisesRegex(ValueError, "缺少文件名字段"):
            list(
                get_bad_accounts.iter_bad_account_names(
                    io.BytesIO(payload),
                    chunk_size=8,
                    encoding="utf-8",
                )
            )

    def test_fetch_bad_accounts_writes_output_file(self):
        payload = b'{"files":[{"name":"expired.json","status":"expired"}]}'
        captured = {}
        original_urlopen = get_bad_accounts.urlopen

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["auth"] = request.get_header("Authorization")
            captured["timeout"] = timeout
            return FakeHttpResponse(payload)

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "all_bad_accounts.txt"
            try:
                get_bad_accounts.urlopen = fake_urlopen
                count = get_bad_accounts.fetch_bad_accounts(
                    get_bad_accounts.FetchConfig(output_path=output_path, chunk_size=4)
                )
            finally:
                get_bad_accounts.urlopen = original_urlopen

            self.assertEqual(count, 1)
            self.assertEqual(output_path.read_text(encoding="utf-8").splitlines(), ["expired.json"])
            self.assertEqual(
                captured,
                {
                    "url": "http://127.0.0.1:8317/v0/management/auth-files",
                    "auth": "Bearer admin123456",
                    "timeout": 30.0,
                },
            )
