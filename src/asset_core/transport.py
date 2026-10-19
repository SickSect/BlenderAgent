"""LLM transport: Ollama and OpenAI-compatible HTTP APIs.

Stdlib only (urllib), so it runs inside Blender's bundled Python as well as
in a normal environment. No threads are started here: the caller iterates
the returned generator at its own pace.

The line parsers are pure functions so they can be unit-tested without a
network.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Dict, Iterable, Iterator, List, Optional

Message = Dict[str, str]


class TransportError(Exception):
    """Any HTTP or protocol failure, with a human readable message."""


@dataclass
class BackendConfig:
    """Connection settings for one backend. Serialisable to/from plain dict."""

    kind: str = "ollama"                  # "ollama" | "openai"
    host: str = "http://localhost:11434"  # Ollama host or OpenAI base URL
    model: str = "qwen2.5-coder:7b"
    api_key: str = ""                     # OpenAI-compatible only
    temperature: float = 0.5
    num_ctx: int = 4096
    timeout: int = 180
    lang: str = "ru"                      # error-message language ("ru"/"en")

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "BackendConfig":
        """Build from a loose dict, ignoring unknown keys (forward compat)."""
        if not data:
            return cls()
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "host": self.host,
            "model": self.model,
            "api_key": self.api_key,
            "temperature": self.temperature,
            "num_ctx": self.num_ctx,
            "timeout": self.timeout,
            "lang": self.lang,
        }


# Transport errors surface in the chat, so they follow the UI language. Kept
# local to this module: the worker runs outside Blender and must not import
# the add-on's i18n tables.
_ERRORS = {
    "ru": {
        "http": "HTTP {code} от {url}: {body}",
        "noconn": "Нет соединения с {url}: {reason}",
        "timeout": "Таймаут ({timeout} c) при запросе к {url}",
        "net": "Сетевая ошибка: {exc}",
        "ollama": "Ollama: {message}",
    },
    "en": {
        "http": "HTTP {code} from {url}: {body}",
        "noconn": "No connection to {url}: {reason}",
        "timeout": "Timeout ({timeout} s) while requesting {url}",
        "net": "Network error: {exc}",
        "ollama": "Ollama: {message}",
    },
}


def _err(lang: str, key: str, **fmt) -> str:
    table = _ERRORS.get(lang or "ru") or _ERRORS["ru"]
    return table[key].format(**fmt)


def stream_chat(config: BackendConfig, messages: List[Message]) -> Iterator[str]:
    """Yield assistant text chunks from the configured backend."""
    if config.kind == "openai":
        yield from _openai_stream(config, messages)
    else:
        yield from _ollama_stream(config, messages)


# --------------------------------------------------------------------------
# Ollama: POST /api/chat, newline-delimited JSON
# --------------------------------------------------------------------------


def _ollama_stream(config: BackendConfig, messages: List[Message]) -> Iterator[str]:
    url = config.host.rstrip("/") + "/api/chat"
    payload = {
        "model": config.model,
        "messages": messages,
        "stream": True,
        "options": {
            "temperature": config.temperature,
            "num_ctx": config.num_ctx,
        },
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with _open(request, config) as response:
        lines = (raw.decode("utf-8", "replace") for raw in response)
        yield from parse_ollama_lines(lines, lang=config.lang)


def parse_ollama_lines(lines: Iterable[str], lang: str = "ru") -> Iterator[str]:
    """Extract assistant content from an Ollama NDJSON stream."""
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("error"):
            raise TransportError(_err(lang, "ollama", message=obj["error"]))
        message = obj.get("message") or {}
        content = message.get("content")
        if content:
            yield content


# --------------------------------------------------------------------------
# OpenAI-compatible: POST /chat/completions, server-sent events
# --------------------------------------------------------------------------


def _openai_stream(config: BackendConfig, messages: List[Message]) -> Iterator[str]:
    url = config.host.rstrip("/") + "/chat/completions"
    payload = {
        "model": config.model,
        "messages": messages,
        "stream": True,
        "temperature": config.temperature,
    }
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with _open(request, config) as response:
        lines = (raw.decode("utf-8", "replace") for raw in response)
        yield from parse_sse_lines(lines)


def parse_sse_lines(lines: Iterable[str]) -> Iterator[str]:
    """Extract assistant content from an OpenAI-style SSE stream."""
    for raw in lines:
        raw = raw.strip()
        if not raw.startswith("data:"):
            continue
        data = raw[len("data:"):].strip()
        if not data or data == "[DONE]":
            if data == "[DONE]":
                return
            continue
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("error"):
            message = obj["error"]
            if isinstance(message, dict):
                message = message.get("message", str(message))
            raise TransportError(str(message))
        choices = obj.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        content = delta.get("content")
        if content:
            yield content


# --------------------------------------------------------------------------
# Shared request plumbing
# --------------------------------------------------------------------------


def _open(request: urllib.request.Request, config: BackendConfig):
    try:
        return urllib.request.urlopen(request, timeout=config.timeout)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", "replace")[:500]
        except Exception:
            body = ""
        raise TransportError(
            _err(config.lang, "http", code=exc.code, url=request.full_url, body=body)
        ) from exc
    except urllib.error.URLError as exc:
        raise TransportError(
            _err(config.lang, "noconn", url=request.full_url, reason=exc.reason)
        ) from exc
    except TimeoutError as exc:
        raise TransportError(
            _err(config.lang, "timeout", timeout=config.timeout, url=request.full_url)
        ) from exc
    except OSError as exc:
        raise TransportError(_err(config.lang, "net", exc=exc)) from exc
