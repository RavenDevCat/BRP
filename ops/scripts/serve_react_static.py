#!/usr/bin/env python3
"""Serve the React build with SPA fallback and a same-origin API proxy."""

from __future__ import annotations

import argparse
import mimetypes
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


class ReactStaticProxyHandler(BaseHTTPRequestHandler):
    server_version = "BRPReactStatic/1.0"

    def do_GET(self) -> None:
        if self._is_api_request():
            self._proxy_api()
            return
        self._serve_static(head_only=False)

    def do_HEAD(self) -> None:
        if self._is_api_request():
            self._proxy_api(head_only=True)
            return
        self._serve_static(head_only=True)

    def do_POST(self) -> None:
        self._proxy_api()

    def do_PUT(self) -> None:
        self._proxy_api()

    def do_PATCH(self) -> None:
        self._proxy_api()

    def do_DELETE(self) -> None:
        self._proxy_api()

    def do_OPTIONS(self) -> None:
        if self._is_api_request():
            self._proxy_api()
            return
        self.send_response(204)
        self.send_header("Allow", "GET, HEAD, OPTIONS")
        self.end_headers()

    def _is_api_request(self) -> bool:
        path = urllib.parse.urlsplit(self.path).path
        return path == "/api" or path.startswith("/api/")

    def _proxy_api(self, head_only: bool = False) -> None:
        if not self._is_api_request():
            self.send_error(404, "Only /api/* requests can be proxied")
            return

        backend_url = self.server.backend_url.rstrip("/")  # type: ignore[attr-defined]
        target_url = f"{backend_url}{self.path}"
        content_length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(content_length) if content_length else None
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS and key.lower() != "host"
        }
        request = urllib.request.Request(
            target_url,
            data=None if head_only else body,
            headers=headers,
            method="HEAD" if head_only else self.command,
        )

        try:
            with urllib.request.urlopen(request, timeout=self.server.proxy_timeout_seconds) as response:  # type: ignore[attr-defined]
                self.send_response(response.status, response.reason)
                self._copy_response_headers(response.headers)
                self.end_headers()
                if not head_only:
                    shutil.copyfileobj(response, self.wfile)
        except urllib.error.HTTPError as exc:
            self.send_response(exc.code, exc.reason)
            self._copy_response_headers(exc.headers)
            self.end_headers()
            if not head_only:
                self.wfile.write(exc.read())
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            message = f"Backend proxy failed: {exc}\n".encode("utf-8", errors="replace")
            self.send_response(502, "Bad Gateway")
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(message)))
            self.end_headers()
            if not head_only:
                self.wfile.write(message)

    def _copy_response_headers(self, headers) -> None:  # type: ignore[no-untyped-def]
        for key, value in headers.items():
            if key.lower() in HOP_BY_HOP_HEADERS:
                continue
            self.send_header(key, value)

    def _serve_static(self, head_only: bool = False) -> None:
        file_path = self._resolve_static_path()
        if file_path is None:
            self.send_error(404, "Static asset not found")
            return

        try:
            stat_result = file_path.stat()
            content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
            if file_path.suffix == ".js":
                content_type = "text/javascript"

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(stat_result.st_size))
            self.send_header("Last-Modified", self.date_time_string(stat_result.st_mtime))
            if file_path.name == "index.html":
                self.send_header("Cache-Control", "no-cache")
            else:
                self.send_header("Cache-Control", "public, max-age=31536000, immutable")
            self.end_headers()

            if not head_only:
                with file_path.open("rb") as handle:
                    shutil.copyfileobj(handle, self.wfile)
        except OSError:
            self.send_error(404, "Static asset not found")

    def _resolve_static_path(self) -> Path | None:
        dist_dir = self.server.dist_dir  # type: ignore[attr-defined]
        index_file = dist_dir / "index.html"
        parsed_path = urllib.parse.urlsplit(self.path).path
        decoded_path = urllib.parse.unquote(parsed_path)

        if decoded_path in {"", "/"}:
            return index_file if index_file.is_file() else None

        relative_path = decoded_path.lstrip("/")
        candidate = (dist_dir / relative_path).resolve()
        try:
            candidate.relative_to(dist_dir)
        except ValueError:
            self.send_error(403, "Forbidden")
            return None

        if candidate.is_dir():
            candidate = candidate / "index.html"
        if candidate.is_file():
            return candidate
        if decoded_path.startswith("/assets/"):
            return None
        return index_file if index_file.is_file() else None


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", default="apps/web/dist", help="React build directory")
    parser.add_argument("--backend-url", default="http://127.0.0.1:8001", help="Backend root URL")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=4173, help="Bind port")
    parser.add_argument("--proxy-timeout", type=float, default=1800.0, help="Backend proxy timeout in seconds")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    dist_dir = Path(args.dist_dir).expanduser().resolve()
    index_file = dist_dir / "index.html"
    if not index_file.is_file():
        print(f"React build not found: {index_file}", file=sys.stderr)
        return 2

    server = ReusableThreadingHTTPServer((args.host, args.port), ReactStaticProxyHandler)
    server.dist_dir = dist_dir
    server.backend_url = args.backend_url
    server.proxy_timeout_seconds = args.proxy_timeout

    print(
        f"Serving {dist_dir} on http://{args.host}:{args.port}, proxying /api/* to {args.backend_url}",
        file=sys.stderr,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping React static server", file=sys.stderr)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
