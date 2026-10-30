from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "gemma4:26b"
RAW_BYTES = re.compile(r"(?:<0x[0-9A-Fa-f]{2}>)+")
TRAILING_BYTES = re.compile(r"(?:<0x[0-9A-Fa-f]{2}>)*(?:<(?:0(?:x[0-9A-Fa-f]{0,2})?)?)?$")
MAX_JSON_TOKENS = 1024
MAX_STORY_TOKENS = 600
SAMPLINGS = ({"temperature": 0, "seed": 7}, {"temperature": 0.6, "seed": 11})


class OllamaError(RuntimeError):
    pass


@dataclass
class Call:
    kind: str
    seconds: float
    prompt_tokens: int
    output_tokens: int


@dataclass
class Ollama:
    model: str = ""
    host: str = ""
    num_ctx: int = 16384
    timeout: float = 300
    calls: list[Call] = field(default_factory=list)

    def __post_init__(self):
        host = self.host or os.environ.get("OLLAMA_HOST") or DEFAULT_HOST
        self.host = (host if host.startswith("http") else "http://" + host).rstrip("/")
        self.model = self.model or os.environ.get("HAUNTED_MODEL") or DEFAULT_MODEL

    def request(self, body: dict):
        request = urllib.request.Request(
            self.host + "/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
        )
        try:
            return urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace").strip()
            if error.code == 404:
                raise OllamaError(f"{detail} (try: ollama pull {body['model']})") from None
            raise OllamaError(f"Ollama answered {error.code}: {detail[:300]}") from None
        except (urllib.error.URLError, ConnectionError, TimeoutError) as error:
            raise OllamaError(f"cannot reach Ollama at {self.host} ({error}); start the Ollama app") from None

    def chat_json(self, kind: str, system: str, user: str, schema: dict, images: list[str] | None = None) -> dict:
        message = {"role": "user", "content": user}
        if images:
            message["images"] = images
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, message],
            "format": schema,
            "stream": False,
            "think": False,
        }
        problem = ""
        for sampling in SAMPLINGS:
            body["options"] = {**sampling, "num_ctx": self.num_ctx, "num_predict": MAX_JSON_TOKENS}
            started = time.monotonic()
            try:
                with self.request(body) as response:
                    reply = json.load(response)
            except (TimeoutError, OSError) as error:
                raise OllamaError(f"Ollama stopped answering: {error}") from None
            self.calls.append(
                Call(kind, time.monotonic() - started, reply.get("prompt_eval_count", 0), reply.get("eval_count", 0))
            )
            if reply.get("done_reason") == "length":
                problem = f"the answer ran past {MAX_JSON_TOKENS} tokens"
                continue
            try:
                return parse_json(repair_bytes(reply.get("message", {}).get("content", "")))
            except ValueError as error:
                problem = str(error)
        raise OllamaError(f"{self.model} gave no usable answer twice: {problem}")

    def stream(self, kind: str, system: str, user: str, temperature: float = 0.8) -> Iterator[str]:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": True,
            "think": False,
            "options": {"temperature": temperature, "num_ctx": self.num_ctx, "num_predict": MAX_STORY_TOKENS},
        }
        started = time.monotonic()
        pending = ""
        try:
            with self.request(body) as response:
                for line in response:
                    if not line.strip():
                        continue
                    part = json.loads(line)
                    if part.get("error"):
                        raise OllamaError(str(part["error"]))
                    pending += part.get("message", {}).get("content", "")
                    cut = TRAILING_BYTES.search(pending).start()
                    if cut:
                        yield repair_bytes(pending[:cut])
                        pending = pending[cut:]
                    if part.get("done"):
                        self.calls.append(
                            Call(
                                kind,
                                time.monotonic() - started,
                                part.get("prompt_eval_count", 0),
                                part.get("eval_count", 0),
                            )
                        )
        except (TimeoutError, OSError, json.JSONDecodeError) as error:
            raise OllamaError(f"Ollama stopped answering: {error}") from None
        if pending:
            yield repair_bytes(pending)

    def seconds(self, kind: str) -> float:
        return sum(call.seconds for call in self.calls if call.kind == kind)


def repair_bytes(text: str) -> str:
    return RAW_BYTES.sub(
        lambda match: bytes.fromhex(re.sub("<0x|>", "", match.group(0))).decode("utf-8", "ignore"), text
    )


def parse_json(content: str) -> dict:
    text = content.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError(f"no JSON object in {content[:200]!r}")
    try:
        value = json.loads(text[start : end + 1])
    except json.JSONDecodeError as error:
        raise ValueError(f"{error} in {content[:200]!r}") from None
    if not isinstance(value, dict):
        raise ValueError("the JSON value is not an object")
    return value
