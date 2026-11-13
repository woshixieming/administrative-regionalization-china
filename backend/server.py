"""同源提供静态前端及只读 PostgreSQL API；不公开源数据目录和配置。"""
import argparse
import json
import logging
import re
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from psycopg_pool import ConnectionPool

from .config import DEFAULT_CONFIG, ROOT, connection_options, load_config
from .repository import ApiError, Repository


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, pool, max_requests):
        self.pool = pool
        self.slots = threading.BoundedSemaphore(max_requests)
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            try:
                request.sendall(b"HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n")
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass  # 请求参数和数据库错误不进入访问日志。

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def send_json(self, status, body):
        encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(encoded)

    def api(self, path, query):
        params = parse_qs(query, keep_blank_values=True)
        if any(len(v) != 1 for v in params.values()):
            raise ApiError(400, "请求参数重复")
        get = lambda key, default="": params.get(key, [default])[0]
        version = get("version") or None
        match = re.fullmatch(r"/api/data/v/([a-f0-9]{24})/(.+)", path)
        if match:
            version, path = match[1], "/api/data/" + match[2]
        with self.server.pool.connection(timeout=5) as connection:
            with connection.transaction():
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                if path == "/api/health":
                    connection.execute("SELECT 1")
                    repo = Repository(connection)
                    return {"status": "ok", "datasetVersion": repo.version}
                repo = Repository(connection, version)
                if path == "/api/status":
                    return repo.document("status.json")
                if path.startswith("/api/data/"):
                    return repo.data(path.removeprefix("/api/data/"))
                if path in ("/api/search", "/api/export"):
                    try:
                        level = int(get("level", "0"))
                    except ValueError:
                        raise ApiError(400, "层级必须为整数") from None
                    if get("history", "1") not in ("0", "1"):
                        raise ApiError(400, "历史筛选无效")
                    history = get("history", "1") == "1"
                    if path == "/api/search":
                        return repo.search(get("q").strip(), get("province"), level, history)
                    return repo.subtree(level, get("code"), history)
                raise ApiError(404, "接口不存在")

    def do_GET(self):
        url = urlsplit(self.path)
        path = unquote(url.path)
        try:
            if path.startswith("/api/"):
                if len(self.path) > 4096:
                    raise ApiError(400, "请求过长")
                return self.send_json(200, self.api(path, url.query))
            # 旧 out/data 即使仍存在也不可绕过数据库访问；仅开放页面和构建资源。
            if any(part in (".", "..") for part in path.split("/")):
                raise ApiError(404, "资源不存在")
            if path not in ("/", "/index.html", "/404.html", "/favicon.ico", "/icon.svg") and not path.startswith("/_next/"):
                raise ApiError(404, "资源不存在")
            target = (self.server.static_root / path.lstrip("/")).resolve()
            if not target.is_relative_to(self.server.static_root) or target.is_symlink():
                raise ApiError(404, "资源不存在")
            return super().do_HEAD() if self.command == "HEAD" else super().do_GET()
        except ApiError as error:
            self.send_json(error.status, {"error": error.message})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.send_json(503, {"error": "数据库服务暂不可用，请检查后端配置、连接及数据导入状态"})

    def do_HEAD(self):
        self.do_GET()

    def list_directory(self, _path):
        self.send_json(404, {"error": "资源不存在"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    args = parser.parse_args()
    config = load_config(args.config)
    pg, settings = config["postgres"], config["server"]
    static = (ROOT / settings["static_dir"]).resolve()
    if not (static / "index.html").is_file():
        raise ValueError("前端构建不存在，请先在 web/ 执行 npm run build")
    logging.getLogger("psycopg.pool").setLevel(logging.CRITICAL)
    with ConnectionPool(kwargs=connection_options(config), min_size=pg.getint("pool_min"),
                        max_size=pg.getint("pool_max"), open=True, timeout=5) as pool:
        pool.wait(timeout=10)
        with Server((settings["host"], settings.getint("port")), partial(Handler, directory=str(static)),
                    pool, settings.getint("max_requests")) as server:
            server.static_root = static
            print(f"行政区划服务已启动，端口 {settings.getint('port')}（Ctrl+C 停止）", flush=True)
            server.serve_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception:
        raise SystemExit("后端启动失败：请检查配置文件、前端构建和 PostgreSQL；未输出连接凭据。") from None
