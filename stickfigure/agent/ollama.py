"""Minimal Ollama client: streaming chat, structured (JSON-schema) chat, embeddings.

The API is async (callers `await` / `async for` from the UI loop), but the HTTP itself is plain
synchronous httpx on worker threads, so network waits and response parsing never touch the
Qt-integrated UI loop.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import threading
from typing import AsyncIterator

import httpx


NUM_CTX = 8192  # room for long memories + chat history (every call must use the same, or Ollama reloads)
THINKING_MODELS = ("qwen3", "deepseek-r1", "magistral")


def no_thinking(model: str) -> dict:
    """Hybrid reasoning models think out loud before answering by default: far too slow for a chatty
    companion, so it's switched off (and JSON output stays clean)."""
    return {"think": False} if model.lower().startswith(THINKING_MODELS) else {}


class OllamaError(RuntimeError):
    pass


class Ollama:
    def __init__(self, base_url: str = "http://127.0.0.1:11434", keep_alive: str = "2h"):
        self.base_url = base_url.rstrip("/")
        self.keep_alive = keep_alive
        self._client = httpx.Client(base_url=self.base_url, timeout=httpx.Timeout(120.0, connect=3.0))
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="ollama")

    async def _call(self, fn, *args):
        return await asyncio.wrap_future(self._pool.submit(fn, *args))

    def _unreachable(self, e: Exception) -> OllamaError:
        return OllamaError(f"can't reach Ollama at {self.base_url} ({e.__class__.__name__})")

    def _post(self, path: str, body: dict) -> httpx.Response:
        try:
            resp = self._client.post(path, json=body)
        except httpx.HTTPError as e:
            raise self._unreachable(e) from e
        if resp.status_code != 200:
            raise OllamaError(f"{resp.status_code}: {resp.text[:200]}")
        return resp

    # -- API ------------------------------------------------------------------------------------

    async def chat_stream(
        self, model: str, messages: list[dict], *, temperature: float = 0.8, max_tokens: int = 200
    ) -> AsyncIterator[str]:
        body = {
            "model": model,
            "messages": messages,
            "stream": True,
            "keep_alive": self.keep_alive,
            "options": {"temperature": temperature, "num_predict": max_tokens, "num_ctx": NUM_CTX},
            **no_thinking(model),
        }
        caller = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        stop = threading.Event()

        def produce() -> None:
            try:
                with self._client.stream("POST", "/api/chat", json=body) as resp:
                    if resp.status_code != 200:
                        raise OllamaError(f"{resp.status_code}: {resp.read().decode(errors='replace')[:200]}")
                    for line in resp.iter_lines():
                        if stop.is_set():
                            return  # consumer went away: closing the response aborts generation
                        if not line:
                            continue
                        chunk = json.loads(line)
                        if "error" in chunk:
                            raise OllamaError(chunk["error"])
                        text = chunk.get("message", {}).get("content", "")
                        if text:
                            caller.call_soon_threadsafe(q.put_nowait, ("chunk", text))
                caller.call_soon_threadsafe(q.put_nowait, ("end", None))
            except httpx.HTTPError as e:
                caller.call_soon_threadsafe(q.put_nowait, ("error", self._unreachable(e)))
            except Exception as e:  # noqa: BLE001 - re-raised on the caller's side
                caller.call_soon_threadsafe(q.put_nowait, ("error", e))

        self._pool.submit(produce)
        try:
            while True:
                kind, value = await q.get()
                if kind == "chunk":
                    yield value
                elif kind == "end":
                    return
                else:
                    raise value
        finally:
            stop.set()  # consumer stopped early (cancelled, or a new reply superseded this one)

    async def chat_json(self, model: str, messages: list[dict], schema: dict, *, temperature: float = 0.1) -> dict:
        body = {
            "model": model,
            "messages": messages,
            "stream": False,
            "format": schema,
            "keep_alive": self.keep_alive,
            "options": {"temperature": temperature, "num_ctx": NUM_CTX},
            **no_thinking(model),
        }
        resp = await self._call(self._post, "/api/chat", body)
        content = resp.json().get("message", {}).get("content", "")
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise OllamaError(f"model returned invalid JSON: {content[:120]!r}") from e

    async def preload(self, model: str, num_ctx: int | None = None) -> None:
        """Load a model into memory without generating anything, so the first real reply is fast.

        num_ctx must match later requests, or Ollama reloads the model with the new context size.
        """
        await self._call(self._post, "/api/generate",
                         {"model": model, "keep_alive": self.keep_alive, "options": {"num_ctx": num_ctx or NUM_CTX}})

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        resp = await self._call(self._post, "/api/embed", {"model": model, "input": texts, "keep_alive": self.keep_alive})
        return resp.json()["embeddings"]

    async def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
        self._client.close()
