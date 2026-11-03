import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit


class FileServer:
    def __init__(
        self,
        files: dict[str, bytes],
        drop_after: int | None = None,
        ranges: bool = True,
        off_by_one: bool = True,
        drop_request: int = 2,
    ):
        self.files = files
        self.drop_after = drop_after
        self.drop_request = drop_request
        self.ranges = ranges
        self.off_by_one = off_by_one
        self.requests: list[tuple[str, str | None]] = []
        self.counts: dict[str, int] = {}
        self.drops = 0
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                return

            def do_GET(self):
                path = urlsplit(self.path).path
                if path.startswith("/redirect/"):
                    self.send_response(302)
                    self.send_header("Location", "/files/" + path[len("/redirect/"):] + "?transaction=1")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                name = path.rsplit("/", 1)[-1]
                data = server.files.get(name)
                header = self.headers.get("Range")
                server.requests.append((name, header))
                server.counts[name] = server.counts.get(name, 0) + 1
                if data is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                start = 0
                end = len(data) - 1
                match = re.match(r"bytes=(\d+)-(\d*)", header or "")
                if match and server.ranges:
                    start = int(match.group(1))
                    if match.group(2):
                        end = min(end, int(match.group(2)))
                    if start >= len(data):
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{len(data)}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    self.send_response(206)
                    shown_end = end - 1 if server.off_by_one and end > start else end
                    self.send_header("Content-Range", f"bytes {start}-{shown_end}/{len(data)}")
                else:
                    self.send_response(200)
                    if server.ranges:
                        self.send_header("Accept-Ranges", "bytes")
                        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
                body = data[start:end + 1]
                self.send_header("Content-Type", "application/x-zip-compressed")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if (
                    server.drop_after is not None
                    and server.drops == 0
                    and server.counts[name] == server.drop_request
                    and len(body) > server.drop_after
                ):
                    server.drops += 1
                    self.wfile.write(body[: server.drop_after])
                    self.wfile.flush()
                    self.close_connection = True
                    self.connection.shutdown(2)
                    return
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.httpd.shutdown()
        self.httpd.server_close()


class LabApiStub:
    def __init__(self, expected_secret: str):
        self.secret = expected_secret
        self.indexed: dict[str, int] = {}
        self.posts: list[str] = []
        self.members: dict[str, int] = {}
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                return

            def _json(self, code, payload):
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                parts = urlsplit(self.path)
                if parts.path == "/version":
                    return self._json(200, {"version": "stub"})
                if parts.path.startswith("/entity/"):
                    entity = unquote(parts.path[len("/entity/"):])
                    known = any(entity == root or entity.startswith(root + "/") for root in stub.members)
                    return self._json(200 if known else 404, {"id": entity})
                if parts.path == "/entities":
                    member_of = parse_qs(parts.query).get("memberOf", [""])[0]
                    if member_of in stub.members:
                        total = stub.members[member_of]
                    elif any(member_of.startswith(root + "/") for root in stub.members):
                        total = 10 ** 6
                    else:
                        total = 0
                    return self._json(200, {"total": total, "entities": []})
                return self._json(404, {})

            def do_POST(self):
                import jwt

                parts = urlsplit(self.path)
                token = (self.headers.get("Authorization") or "")[7:]
                try:
                    claims = jwt.decode(token, stub.secret, algorithms=["HS256"])
                except jwt.PyJWTError:
                    return self._json(401, {})
                if claims.get("role") not in ("ADMIN", "SUPER_ADMIN"):
                    return self._json(401, {})
                if parts.path.startswith("/admin/index/"):
                    pattern = unquote(parts.path[len("/admin/index/"):])
                    stub.posts.append(pattern)
                    for root, count in stub.indexed.items():
                        if re.match("^" + pattern, root):
                            stub.members[root] = count
                    return self._json(202, {"isIndexed": False})
                return self._json(404, {})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.httpd.shutdown()
        self.httpd.server_close()
