"""Explicit competition filenames and the shared, restricted JPEG asset."""

from __future__ import annotations

import http.client
import importlib
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from backend.storage import DataError, Database


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DETAILS = {
    "name": "文件名测试赛", "start_date": "2026-10-13", "end_date": "2026-10-14", "venue": "体育馆",
}


class CompetitionFilenameTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = PROJECT_ROOT / "tmp" / "tests"
        scratch.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db = Database(self.root)

    def test_omitted_filename_generates_usable_identifier(self) -> None:
        with patch("backend.storage.secrets.token_hex", return_value="ab12cd"):
            result = self.db.create_competition(DETAILS)
        slug = result["competition"]["slug"]
        self.assertEqual(slug, "competition-20261013-ab12cd")
        self.assertTrue((self.db.base / slug / "meta.json").is_file())
        self.assertEqual(self.db.list_competitions()["competitions"][0]["slug"], slug)

    def test_custom_filename_persists_and_collides_without_overwriting(self) -> None:
        result = self.db.create_competition({**DETAILS, "slug": "pku-cup-2026"})
        before = (self.db.base / "pku-cup-2026" / "meta.json").read_bytes()
        self.assertEqual(result["competition"]["slug"], "pku-cup-2026")
        with self.assertRaises(DataError) as raised:
            self.db.create_competition({**DETAILS, "name": "覆盖尝试", "slug": "pku-cup-2026"})
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual((self.db.base / "pku-cup-2026" / "meta.json").read_bytes(), before)
        self.assertEqual(len(self.db.list_competitions()["competitions"]), 1)

    def test_explicit_empty_or_invalid_filename_cannot_fall_back_or_escape(self) -> None:
        for slug in ("", None, False, 0, " ", "比赛", "A-cup", "1-cup", "a_b", "a--b", "a-", "../escape", "a/b", "a\\b", "C:\\escape", "a.json", "con", "nul", "com1", "lpt9"):
            with self.subTest(slug=slug), self.assertRaises(DataError):
                self.db.create_competition({**DETAILS, "slug": slug})
        self.assertEqual(self.db.list_competitions(), {"competitions": []})
        self.assertEqual([item.name for item in self.db.base.iterdir()], ["index.json"])

    def test_filename_cannot_be_renamed_after_creation(self) -> None:
        self.db.create_competition({**DETAILS, "slug": "immutable-cup"})
        with self.assertRaisesRegex(DataError, "创建后不可修改"):
            self.db.update_competition("immutable-cup", {"slug": "renamed-cup", "name": "也不应保存"})
        self.assertFalse((self.db.base / "renamed-cup").exists())
        self.assertEqual(self.db.get_competition("immutable-cup")["competition"]["name"], DETAILS["name"])
        self.assertEqual(self.db.update_competition("immutable-cup", {"slug": "immutable-cup", "name": "新名称"})["competition"]["name"], "新名称")


class IconHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = PROJECT_ROOT / "tmp" / "tests"
        scratch.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        database = Database(self.root)
        webpage = self.root / "webpage"
        self.icon = b"\xff\xd8revision-seven-icon\xff\xd9"
        (webpage / "icon.jpg").write_bytes(self.icon)
        (webpage / "index.html").write_text('<html data-mode="public"><link rel="icon" type="image/jpeg" href="./icon.jpg"><link href="./style.css"><script src="./app.js"></script></html>', encoding="utf-8")
        (webpage / "style.css").write_text("body {}", encoding="utf-8")
        (webpage / "app.js").write_text("/* shared app */", encoding="utf-8")
        (webpage / "secret.txt").write_text("should not be served", encoding="utf-8")
        # Importing the HTTP module initializes DATA. Point that initialization
        # at this fixture, without loading or migrating the live workspace.
        with patch("backend.storage.Database", return_value=database), patch("backend.storage.ROOT", self.root):
            module = importlib.import_module("backend.server")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), module.AdminHandler)
        port = self.server.server_address[1]
        for key, value in {"ROOT": self.root, "DATA": database, "ALLOWED_HOSTS": {f"127.0.0.1:{port}"}}.items():
            replacement = patch.object(module, key, value)
            replacement.start()
            self.addCleanup(replacement.stop)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path: str, method: str = "GET", body: dict | None = None) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        connection.request(method, path, payload, {"Content-Type": "application/json"} if body is not None else {})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_jpeg_is_served_with_exact_bytes_and_mime(self) -> None:
        status, headers, body = self.request("/icon.jpg")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/jpeg")
        self.assertEqual(headers["Content-Length"], str(len(self.icon)))
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(body, self.icon)

    def test_admin_root_and_alias_rewrite_favicon_to_same_icon(self) -> None:
        for path in ("/", "/admin", "/admin/index.html"):
            with self.subTest(path=path):
                status, headers, body = self.request(path)
                self.assertEqual(status, 200)
                self.assertTrue(headers["Content-Type"].startswith("text/html"))
                self.assertIn(b'data-mode="admin"', body)
                self.assertIn(b'href="/icon.jpg"', body)
                self.assertIn(b'href="/assets/style.css"', body)
                self.assertIn(b'src="/assets/app.js"', body)

    def test_icon_route_does_not_allow_arbitrary_files_or_paths(self) -> None:
        for path in ("/secret.txt", "/assets/secret.txt", "/assets/icon.jpg", "/../webpage/icon.jpg", "/%2e%2e/webpage/icon.jpg", "/icon.jpg/secret.txt", "/vibecoding/Figure/icon.jpg"):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 404)
        status, _, body = self.request("/icon.jpg", "POST", {})
        self.assertEqual(status, 405)
        self.assertIn("只支持 GET", body.decode("utf-8"))

    def test_http_create_rejects_blank_custom_filename(self) -> None:
        status, _, body = self.request("/api/competitions", "POST", {**DETAILS, "slug": ""})
        self.assertEqual(status, 400)
        self.assertIn("文件名", json.loads(body)["error"])
        self.assertEqual(json.loads(self.request("/api/competitions")[2]), {"competitions": []})


if __name__ == "__main__":
    unittest.main()
