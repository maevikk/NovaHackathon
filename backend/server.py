"""Локальный HTTP-сервер PROCESS LAB (Python stdlib, без pip).

Запуск ИЗ КОРНЯ РЕПОЗИТОРИЯ:
    python -m backend.server
Открыть: http://127.0.0.1:8765/

Это сервер для локального хакатонного прототипа. Без входа и HTTPS;
не размещать в публичном интернете и не хранить персональные данные.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from .analytics import analyze, compare
from .engine import simulate
from .validation import ModelError, validate_model

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
DEMO = ROOT / "data" / "factory_demo_v3.json"
MAX_JSON = 750_000
STATIC_FILES = {
    "/": "index.html", "/index.html": "index.html", "/frontend/index.html": "index.html",
    "/styles.css": "styles.css", "/frontend/styles.css": "styles.css",
    "/app.js": "app.js", "/frontend/app.js": "app.js",
    "/backend_sync.js": "backend_sync.js", "/frontend/backend_sync.js": "backend_sync.js",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "ProcessLabDemo/1.0"

    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def json_response(self, status, payload):
        raw = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        route = urlsplit(self.path).path
        if route == "/api/v1/health":
            return self.json_response(200, {"status": "ok", "service": "process-lab", "apiVersion": "1.0",
                                            "simulation": "python_event_driven"})
        if route == "/api/v1/demo":
            return self.json_response(200, json.loads(DEMO.read_text(encoding="utf-8")))
        filename = STATIC_FILES.get(route)
        if filename is None:
            return self.json_response(404, {"error": "not_found", "message": "Адрес не найден"})
        file_path = FRONTEND / filename
        if not file_path.is_file():
            return self.json_response(404, {"error": "file_missing", "message": "Отсутствует frontend/" + filename})
        payload = file_path.read_bytes()
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", mime + "; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline' blob: data:; connect-src 'self'; object-src 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(payload)

    def read_json(self):
        if "application/json" not in self.headers.get("Content-Type", ""):
            raise ModelError("Требуется заголовок Content-Type: application/json")
        content_length = self.headers.get("Content-Length", "")
        if not content_length.isdigit():
            raise ModelError("Не задан Content-Length")
        length = int(content_length)
        if length > MAX_JSON:
            raise ModelError("Максимальный размер запроса — 750 КБ")
        if length < 2:
            raise ModelError("Пустой JSON-запрос")
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"), parse_constant=lambda x: (_ for _ in ()).throw(ValueError("non-finite")))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise ModelError("Некорректный JSON") from error
        if not isinstance(data, dict):
            raise ModelError("Ожидался JSON-объект")
        return data

    def do_POST(self):
        route = urlsplit(self.path).path
        if route not in ("/api/v1/validate", "/api/v1/simulate", "/api/v1/compare"):
            return self.json_response(404, {"error": "not_found", "message": "Метод API не найден"})
        try:
            request = self.read_json()
            if route == "/api/v1/compare":
                base = request.get("baseline")
                candidate = request.get("candidate")
                if base is None or candidate is None:
                    raise ModelError("Сравнение требует baseline и candidate")
                return self.json_response(200, compare(base, candidate))
            model = request.get("model", request)
            if route == "/api/v1/validate":
                graph = validate_model(model)
                return self.json_response(200, {"valid": True, "operationCount": len(graph["ops"]),
                                               "materialEdges": len(model["edges"]), "schema": "kpsc-studio-v3"})
            result = simulate(model)
            # Дополнительные метрики НЕ меняют поля старого фронтенда.
            result["methodology"] = analyze(model, result)
            return self.json_response(200, result)
        except ModelError as err:
            return self.json_response(422, {"error": "invalid_model", "message": str(err)})
        except Exception as err:
            # Логи полезны разработчику; не отдаём стек клиенту.
            self.log_error("Server error: %r", err)
            return self.json_response(500, {"error": "internal_error", "message": "Внутренняя ошибка расчётного сервера"})


def main(argv=None):
    parser = argparse.ArgumentParser(description="PROCESS LAB: локальный API + frontend")
    parser.add_argument("--host", default="127.0.0.1", help="По умолчанию доступ только с этого компьютера")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        parser.error("Прототип нельзя запускать на публичном интерфейсе без авторизации и TLS")
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print("PROCESS LAB — backend запущен: http://%s:%s/" % (args.host, args.port))
    try:
        httpd.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        print("Остановка сервера")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
