#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import secrets
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from haunted import world as worlds
from haunted.engine import Game
from haunted.master import Master
from haunted.ollama import Ollama, OllamaError

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
SAMPLES = ROOT / "worlds"
MAX_JSON_BYTES = 200 * 1024
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_ACTION = 300
LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")
IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png"}
STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".jpg": "image/jpeg",
    ".png": "image/png",
}


class BadRequest(Exception):
    pass


class House:
    def __init__(self, data: Path, master: Master):
        self.data = data
        self.master = master
        self.games: dict[str, Game] = {}
        self.locks: dict[str, threading.Lock] = {}
        self.model_lock = threading.Lock()
        self.registry_lock = threading.Lock()

    @property
    def world_path(self) -> Path:
        return self.data / "world.json"

    @property
    def drawings(self) -> Path:
        return self.data / "drawings"

    def world(self) -> worlds.World:
        if self.world_path.exists():
            return worlds.load(self.world_path)
        return worlds.load(SAMPLES / "sample-en.json")

    def samples(self) -> dict:
        return {
            path.stem.removeprefix("sample-"): worlds.load(path).as_dict()
            for path in sorted(SAMPLES.glob("sample-*.json"))
        }

    def new_game(self) -> Game:
        game = Game(self.world())
        with self.registry_lock:
            self.games[game.id] = game
            self.locks[game.id] = threading.Lock()
        return game

    def game(self, game_id: str) -> Game:
        with self.registry_lock:
            if game_id in self.games:
                return self.games[game_id]
            path = self.data / "games" / f"{game_id}.json"
            if not game_id.isalnum() or not path.exists():
                raise BadRequest("unknown game")
            data = json.loads(path.read_text(encoding="utf-8"))
            game = Game.restore(worlds.parse(data["world"]), data)
            self.games[game.id] = game
            self.locks[game.id] = threading.Lock()
            return game

    def keep(self, game: Game) -> None:
        folder = self.data / "games"
        folder.mkdir(parents=True, exist_ok=True)
        temporary = folder / f"{game.id}.tmp"
        temporary.write_text(json.dumps(game.snapshot(), ensure_ascii=False, indent=1), encoding="utf-8")
        temporary.replace(folder / f"{game.id}.json")


class Handler(BaseHTTPRequestHandler):
    house: House
    listening = True

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        url = urlparse(self.path)
        try:
            if url.path == "/":
                return self.send_file(STATIC / "index.html")
            if url.path.startswith("/static/"):
                return self.send_file(STATIC / url.path.removeprefix("/static/"))
            if url.path.startswith("/drawings/"):
                name = url.path.removeprefix("/drawings/")
                if not worlds.drawing_name(name):
                    raise BadRequest("unknown drawing")
                return self.send_file(self.house.drawings / name)
            if url.path == "/api/world":
                return self.send_json({"world": self.house.world().as_dict(), "samples": self.house.samples()})
            if url.path == "/api/game":
                game_id = parse_qs(url.query).get("id", [""])[0]
                game = self.house.game(game_id)
                return self.send_json({"state": game.state(), "turns": game.turns})
            self.send_error(HTTPStatus.NOT_FOUND)
        except BadRequest as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except worlds.WorldError as error:
            self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_PUT(self):
        try:
            self.check_origin()
            if urlparse(self.path).path != "/api/world":
                return self.send_error(HTTPStatus.NOT_FOUND)
            world = worlds.parse(self.read_json())
            for creature in world.creatures:
                if creature.drawing and not (self.house.drawings / creature.drawing).exists():
                    creature.drawing = ""
            worlds.save(world, self.house.world_path)
            self.send_json({"world": world.as_dict()})
        except (BadRequest, worlds.WorldError) as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            self.check_origin()
            if path == "/api/game":
                game = self.house.new_game()
                return self.stream(game, lambda: self.house.master.opening(game))
            if path == "/api/turn":
                body = self.read_json()
                game = self.house.game(str(body.get("game") or ""))
                action = " ".join(str(body.get("action") or "").split())[:MAX_ACTION]
                if not action:
                    raise BadRequest("write what you want to do")
                if game.ending:
                    raise BadRequest("this game is over")
                return self.stream(game, lambda: self.house.master.turn(game, action))
            if path == "/api/drawing":
                return self.read_drawing()
            self.send_error(HTTPStatus.NOT_FOUND)
        except (BadRequest, worlds.WorldError) as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except OllamaError as error:
            self.send_json({"error": str(error)}, HTTPStatus.SERVICE_UNAVAILABLE)

    def stream(self, game: Game, events):
        lock = self.house.locks[game.id]
        if not lock.acquire(blocking=False):
            raise BadRequest("the Game Master is still telling the last turn")
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.send_line({"type": "game", "id": game.id})
            with self.house.model_lock:
                try:
                    for event in events():
                        self.send_line(event)
                except OllamaError as error:
                    self.send_line({"type": "error", "error": str(error)})
                finally:
                    self.house.keep(game)
        except ConnectionError:
            pass
        finally:
            lock.release()

    def read_drawing(self):
        kind = self.headers.get("Content-Type", "").split(";")[0].strip()
        if kind not in IMAGE_TYPES:
            raise BadRequest("send a JPEG or PNG photo of the drawing")
        image = self.read_body(MAX_IMAGE_BYTES)
        language = parse_qs(urlparse(self.path).query).get("language", ["en"])[0]
        if language not in worlds.LANGUAGES:
            raise BadRequest("unknown language")
        with self.house.model_lock:
            sheet = self.house.master.draw(base64.b64encode(image).decode(), language)
        name = secrets.token_hex(8) + IMAGE_TYPES[kind]
        self.house.drawings.mkdir(parents=True, exist_ok=True)
        (self.house.drawings / name).write_bytes(image)
        self.send_json({"sheet": sheet, "drawing": name})

    def check_origin(self):
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        origin = self.headers.get("Origin")
        if self.server.local_only and host not in LOCAL_HOSTS:
            raise BadRequest("unexpected host")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            raise BadRequest("unexpected origin")

    def read_body(self, limit: int) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as error:
            raise BadRequest("bad Content-Length") from error
        if not 0 < length <= limit:
            raise BadRequest("empty or too large request")
        return self.rfile.read(length)

    def read_json(self) -> dict:
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            raise BadRequest("send JSON")
        try:
            value = json.loads(self.read_body(MAX_JSON_BYTES))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BadRequest("bad JSON") from error
        if not isinstance(value, dict):
            raise BadRequest("send a JSON object")
        return value

    def send_line(self, event: dict):
        if not self.listening:
            return
        try:
            self.wfile.write((json.dumps(event, ensure_ascii=False) + "\n").encode())
            self.wfile.flush()
        except ConnectionError:
            self.listening = False

    def send_json(self, value: dict, status: HTTPStatus = HTTPStatus.OK):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path):
        root = self.house.drawings if path.parent == self.house.drawings else STATIC
        resolved = path.resolve()
        if not resolved.is_file() or root.resolve() not in resolved.parents:
            return self.send_error(HTTPStatus.NOT_FOUND)
        body = resolved.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", STATIC_TYPES.get(resolved.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def main():
    parser = argparse.ArgumentParser(description="A haunted house with Gemma as the Game Master.")
    parser.add_argument("--port", type=int, default=8800)
    parser.add_argument("--lan", action="store_true", help="also accept other devices on the local network")
    parser.add_argument("--model", default="", help="Ollama model, gemma4:26b by default")
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    Handler.house = House(args.data, Master(Ollama(model=args.model)))
    server = ThreadingHTTPServer(("0.0.0.0" if args.lan else "127.0.0.1", args.port), Handler)
    server.local_only = not args.lan
    url = f"http://localhost:{args.port}/"
    print(f"The Haunted House is open at {url} (model {Handler.house.master.ollama.model})")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
