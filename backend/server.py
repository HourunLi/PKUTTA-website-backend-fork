"""Loopback-only HTTP administration UI and JSON API.

Run with ``python -m backend.server`` and open http://localhost:4000/.
Admin and public views share ``webpage`` assets and its live JSON database.
"""

from __future__ import annotations

import json
import mimetypes
import re
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .publish import Publisher, SubmissionBusy
from .storage import ROOT, DataError, Database


ADDRESS = ("127.0.0.1", 4000)
MAX_BODY_BYTES = 10 * 1024 * 1024
ALLOWED_HOSTS = {"localhost:4000", "127.0.0.1:4000", "[::1]:4000"}
DATA = Database(ROOT)
WRITE_LOCK = threading.RLock()
PUBLISHER = Publisher(ROOT, WRITE_LOCK)


class AdminHandler(BaseHTTPRequestHandler):
    """Serve static admin files and route data changes to ``Database``."""

    server_version = "PKUTTA/1.0"

    def _json(self, status: int, payload: object) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(encoded)

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _validate_request_host(self) -> None:
        if self.headers.get("Host", "").lower() not in ALLOWED_HOSTS:
            raise DataError("只允许从 localhost 访问后台", 403)
        origin = self.headers.get("Origin")
        if origin and origin.lower() not in {f"http://{host}" for host in ALLOWED_HOSTS}:
            raise DataError("跨站请求被拒绝", 403)

    def _body(self) -> dict[str, object]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise DataError("Content-Length 无效") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise DataError("请求体过大", 413)
        if length == 0:
            return {}
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise DataError("请求体须为 application/json")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DataError("请求体不是有效的 UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise DataError("JSON 请求体必须是对象")
        return payload

    def _path(self) -> list[str]:
        raw = urlsplit(self.path).path
        try:
            decoded = unquote(raw, errors="strict")
        except UnicodeDecodeError as exc:
            raise DataError("URL 编码无效") from exc
        return [part for part in decoded.split("/") if part]

    def _route_api(self, parts: list[str]) -> object:
        method = self.command
        if parts == ["api", "health"] and method == "GET":
            return {"ok": True}
        if parts == ["api", "submit"]:
            if method == "GET":
                return PUBLISHER.state()
            if method == "POST":
                self._body()
                try:
                    return PUBLISHER.start()
                except SubmissionBusy as exc:
                    raise DataError(str(exc), 409) from exc
        if parts == ["api", "data"] and method == "GET":
            return DATA.snapshot()
        if parts == ["api", "competitions"]:
            if method == "GET":
                return DATA.list_competitions()
            if method == "POST":
                return DATA.create_competition(self._body())
        if len(parts) >= 3 and parts[:2] == ["api", "competitions"]:
            competition = parts[2]
            if len(parts) == 3:
                if method == "GET":
                    return DATA.get_competition(competition)
                if method == "PATCH":
                    return DATA.update_competition(competition, self._body())
                if method == "DELETE":
                    return DATA.delete_competition(competition)
            if len(parts) == 4 and parts[3] == "events" and method == "POST":
                return DATA.create_event(competition, self._body())
            if len(parts) >= 5 and parts[3] == "events":
                event = parts[4]
                if len(parts) == 5:
                    if method == "GET":
                        return DATA.get_event(competition, event)
                    if method == "DELETE":
                        return DATA.delete_event(competition, event)
                if (
                    len(parts) == 7
                    and parts[5] == "groups"
                    and re.fullmatch(r"[1-9][0-9]*", parts[6])
                    and method == "PATCH"
                ):
                    return DATA.update_group(competition, event, int(parts[6]), self._body())
                if len(parts) == 7 and parts[5:7] == ["round-robin", "schedule"] and method == "PATCH":
                    return DATA.update_round_robin_schedule(competition, event, self._body())
                if len(parts) >= 7 and parts[5] == "stages":
                    stage = parts[6]
                    if len(parts) == 8 and parts[7] == "draw" and method == "POST":
                        self._body()
                        return DATA.draw_stage(competition, event, stage)
                    if len(parts) >= 9 and parts[7] == "matches":
                        match_id = parts[8]
                        if len(parts) == 9 and method == "PATCH":
                            return DATA.mutate_match(competition, event, stage, match_id, "edit_match", self._body())
                        if len(parts) == 10 and parts[9] == "games" and method == "POST":
                            return DATA.mutate_match(competition, event, stage, match_id, "add_game", self._body())
                        if len(parts) == 11 and parts[9] == "games" and re.fullmatch(r"0|[1-9][0-9]*", parts[10]):
                            game_index = int(parts[10])
                            if method == "PATCH":
                                return DATA.mutate_match(competition, event, stage, match_id, "edit_game", self._body(), game_index)
                            if method == "DELETE":
                                return DATA.mutate_match(competition, event, stage, match_id, "delete_game", game_index=game_index)
        raise DataError("API 路径或方法不存在", 404)

    def _static(self, parts: list[str]) -> None:
        if parts in ([], ["index.html"], ["admin"], ["admin", "index.html"]):
            source = ROOT / "webpage" / "index.html"
            try:
                html = source.read_text(encoding="utf-8")
            except OSError as exc:
                raise DataError("后台页面资源不存在", 500) from exc
            html = html.replace("__MODE__", "admin").replace("__ASSET_PREFIX__", "/assets/")
            html = html.replace('data-mode="public"', 'data-mode="admin"')
            html = html.replace('href="./style.css"', 'href="/assets/style.css"')
            html = html.replace('href="./icon.jpg"', 'href="/icon.jpg"')
            html = html.replace('src="./app.js"', 'src="/assets/app.js"')
            encoded = html.encode("utf-8")
            mime = "text/html; charset=utf-8"
        elif parts in (["assets", "app.js"], ["assets", "style.css"], ["app.js"], ["style.css"]):
            filename = parts[-1]
            source = ROOT / "webpage" / filename
            try:
                encoded = source.read_bytes()
            except OSError as exc:
                raise DataError("后台页面资源不存在", 500) from exc
            mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            if mime.startswith("text/") or mime == "application/javascript":
                mime += "; charset=utf-8"
        elif parts == ["icon.jpg"]:
            try:
                encoded = (ROOT / "webpage" / "icon.jpg").read_bytes()
            except OSError as exc:
                raise DataError("网页图标不存在", 404) from exc
            mime = "image/jpeg"
        else:
            raise DataError("页面不存在", 404)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(encoded)

    def _handle(self) -> None:
        try:
            self._validate_request_host()
            parts = self._path()
            if parts[:1] == ["api"]:
                if self.command not in ("GET", "POST", "PATCH", "DELETE"):
                    raise DataError("不支持的 HTTP 方法", 405)
                if parts == ["api", "submit"]:
                    result = self._route_api(parts)
                else:
                    with WRITE_LOCK:
                        result = self._route_api(parts)
                status = HTTPStatus.ACCEPTED if parts == ["api", "submit"] and self.command == "POST" else HTTPStatus.OK
                self._json(status, result)
            elif self.command == "GET":
                self._static(parts)
            else:
                raise DataError("页面只支持 GET", 405)
        except DataError as exc:
            self._error(exc.status, str(exc))
        except Exception as exc:  # The request boundary must always return JSON on failure.
            self.log_error("request failed: %s", exc)
            self._error(500, f"服务器错误：{exc}")

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle()

    def do_POST(self) -> None:  # noqa: N802
        self._handle()

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle()

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle()


def main() -> None:
    """Run the local service until interrupted from the terminal."""
    server = ThreadingHTTPServer(ADDRESS, AdminHandler)
    print(f"PKUTTA admin: http://localhost:{ADDRESS[1]}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
