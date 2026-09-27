"""First-run requirement checks (Ollama + models), with the network faked."""

import json
from contextlib import contextmanager
from dataclasses import replace

import httpx

from stickfigure import firstrun
from stickfigure.config import Config


class Resp:
    def __init__(self, status=200, data=None, lines=()):
        self.status_code, self._data, self._lines = status, data, lines

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code != 200:
            raise httpx.HTTPStatusError("bad", request=None, response=None)

    def iter_lines(self):
        yield from self._lines


def fake_ollama(monkeypatch, running=True, models=()):
    def get(url, timeout=None):
        if not running:
            raise httpx.ConnectError("refused")
        if url.endswith("/api/version"):
            return Resp(200, {"version": "0.34"})
        return Resp(200, {"models": [{"name": m} for m in models]})

    monkeypatch.setattr(firstrun.httpx, "get", get)


def test_required_models_are_deduplicated():
    cfg = replace(Config(), chat_model="qwen3:8b", extract_model="qwen3:8b", action_model="qwen3:8b",
                  code_model="qwen3:8b", embed_model="embeddinggemma")
    assert firstrun.required_models(cfg) == ["qwen3:8b", "embeddinggemma"]


def test_ready_when_running_with_all_models(monkeypatch):
    fake_ollama(monkeypatch, models=["qwen3:8b", "embeddinggemma:latest"])
    st = firstrun.check(replace(Config(), chat_model="qwen3:8b", extract_model="qwen3:8b", action_model="qwen3:8b",
                                code_model="qwen3:8b", embed_model="embeddinggemma"))
    assert st.ready and st.missing_models == []


def test_reports_missing_models(monkeypatch):
    fake_ollama(monkeypatch, models=["embeddinggemma:latest"])
    st = firstrun.check(replace(Config(), chat_model="qwen3:8b", extract_model="qwen3:8b", action_model="qwen3:8b",
                                code_model="qwen3:8b", embed_model="embeddinggemma"))
    assert not st.ready and st.missing_models == ["qwen3:8b"]


def test_not_installed_and_not_running(monkeypatch):
    fake_ollama(monkeypatch, running=False)
    monkeypatch.setattr(firstrun, "ollama_exe", lambda: None)
    st = firstrun.check()
    assert not st.ollama_installed and not st.ollama_running and not st.ready


def test_pull_reports_overall_progress(monkeypatch):
    lines = [json.dumps(x) for x in (
        {"status": "pulling manifest"},
        {"status": "pulling a", "digest": "a", "total": 100, "completed": 50},
        {"status": "pulling b", "digest": "b", "total": 300, "completed": 0},
        {"status": "pulling b", "digest": "b", "total": 300, "completed": 300},
        {"status": "pulling a", "digest": "a", "total": 100, "completed": 100},
        {"status": "success"},
    )]

    @contextmanager
    def stream(method, url, json=None, timeout=None):
        assert url.endswith("/api/pull") and json["model"] == "qwen3:8b"
        yield Resp(200, lines=lines)

    monkeypatch.setattr(firstrun.httpx, "stream", stream)
    seen = []
    firstrun.pull_model("qwen3:8b", lambda f, s: seen.append(round(f, 3)))
    assert seen[1] == 0.5 and seen[2] == 0.125 and seen[-1] == 1.0


def test_pull_surfaces_errors(monkeypatch):
    @contextmanager
    def stream(method, url, json=None, timeout=None):
        yield Resp(200, lines=['{"error": "model not found"}'])

    monkeypatch.setattr(firstrun.httpx, "stream", stream)
    try:
        firstrun.pull_model("nope", lambda f, s: None)
    except RuntimeError as e:
        assert "not found" in str(e)
    else:
        raise AssertionError("expected an error")
