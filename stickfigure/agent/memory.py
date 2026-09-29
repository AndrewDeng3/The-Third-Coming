"""Persistent memory: chat history, long-term facts with vector recall (sqlite-vec), and key-value state.

All in one SQLite file. Embedding is injected (async callable) so tests can use a fake.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

import sqlite_vec

Embedder = Callable[[list[str], str], Awaitable[list[list[float]]]]  # (texts, "query"|"document")

DUPLICATE_DISTANCE = 0.08  # cosine distance under which a new fact replaces an old one


@dataclass(frozen=True)
class Fact:
    id: int
    text: str
    ts: float
    distance: float = 0.0


class Memory:
    def __init__(self, path: Path | str, embed: Embedder):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.enable_load_extension(True)
        sqlite_vec.load(self.db)
        self.db.enable_load_extension(False)
        self.embed = embed
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, role TEXT NOT NULL, content TEXT NOT NULL, ts REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS facts(id INTEGER PRIMARY KEY, text TEXT NOT NULL, ts REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS episodes(id INTEGER PRIMARY KEY, text TEXT NOT NULL, ts REAL NOT NULL);
            """
        )
        self.db.commit()

    # -- key/value ------------------------------------------------------------------

    def get(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key: str, value) -> None:
        self.db.execute("INSERT OR REPLACE INTO kv(key, value) VALUES (?, ?)", (key, json.dumps(value)))
        self.db.commit()

    # -- chat history -------------------------------------------------------------------

    def add_message(self, role: str, content: str) -> None:
        self.db.execute("INSERT INTO messages(role, content, ts) VALUES (?, ?, ?)", (role, content, time.time()))
        self.db.commit()

    def recent_messages(self, n: int, asides: bool = False) -> list[dict]:
        """The last `n` chat turns. `asides` (off-the-cuff remarks it made on its own, role "aside") are shown
        in the chat window but kept out of the model's conversation history."""
        where = "" if asides else "WHERE role != 'aside'"
        rows = self.db.execute(f"SELECT role, content FROM messages {where} ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return [{"role": r, "content": c} for r, c in reversed(rows)]

    def clear_messages(self) -> None:
        """Wipe the conversation history (long-term facts and mood are kept)."""
        self.db.execute("DELETE FROM messages")
        self.db.commit()

    def last_message_time(self) -> float | None:
        row = self.db.execute("SELECT ts FROM messages ORDER BY id DESC LIMIT 1").fetchone()
        return row[0] if row else None

    # -- facts -------------------------------------------------------------------------------

    def _ensure_vec_table(self, dim: int) -> None:
        stored = self.get("embed_dim")
        if stored is not None and stored != dim:
            # Embedding model changed: old vectors are meaningless, re-embed lazily by dropping them.
            self.db.execute("DROP TABLE IF EXISTS fact_vec")
        self.db.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS fact_vec USING vec0(embedding float[{dim}] distance_metric=cosine)"
        )
        self.put("embed_dim", dim)

    def _has_vec(self, table: str = "fact_vec") -> bool:
        return self.db.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (table,)).fetchone() is not None

    async def add_fact(self, text: str) -> int:
        """Store a fact; a near-duplicate of an existing fact replaces it instead. Returns the fact id."""
        text = text.strip()
        (vec,) = await self.embed([text], "document")
        self._ensure_vec_table(len(vec))
        blob = sqlite_vec.serialize_float32(vec)
        dup = self.db.execute(
            "SELECT rowid, distance FROM fact_vec WHERE embedding MATCH ? AND k = 1", (blob,)
        ).fetchone()
        now = time.time()
        if dup and dup[1] < DUPLICATE_DISTANCE:
            fid = dup[0]
            self.db.execute("UPDATE facts SET text = ?, ts = ? WHERE id = ?", (text, now, fid))
            self.db.execute("DELETE FROM fact_vec WHERE rowid = ?", (fid,))
            self.db.execute("INSERT INTO fact_vec(rowid, embedding) VALUES (?, ?)", (fid, blob))
        else:
            fid = self.db.execute("INSERT INTO facts(text, ts) VALUES (?, ?)", (text, now)).lastrowid
            self.db.execute("INSERT INTO fact_vec(rowid, embedding) VALUES (?, ?)", (fid, blob))
        self.db.commit()
        return fid

    def remove_fact(self, fid: int) -> None:
        self.db.execute("DELETE FROM facts WHERE id = ?", (fid,))
        if self._has_vec():
            self.db.execute("DELETE FROM fact_vec WHERE rowid = ?", (fid,))
        self.db.commit()

    async def recall(self, query: str, k: int = 5, max_distance: float = 0.75) -> list[Fact]:
        if not self._has_vec() or not query.strip():
            return []
        (vec,) = await self.embed([query], "query")
        rows = self.db.execute(
            """SELECT f.id, f.text, f.ts, v.distance
               FROM (SELECT rowid, distance FROM fact_vec WHERE embedding MATCH ? AND k = ?) v
               JOIN facts f ON f.id = v.rowid ORDER BY v.distance""",
            (sqlite_vec.serialize_float32(vec), k),
        ).fetchall()
        return [Fact(*r) for r in rows if r[3] <= max_distance]

    def recent_facts(self, n: int) -> list[Fact]:
        rows = self.db.execute("SELECT id, text, ts FROM facts ORDER BY ts DESC LIMIT ?", (n,)).fetchall()
        return [Fact(*r) for r in rows]

    def all_facts(self) -> list[Fact]:
        return [Fact(*r) for r in self.db.execute("SELECT id, text, ts FROM facts ORDER BY ts").fetchall()]

    # -- episodes: whole past exchanges, recalled by meaning (long-term conversational memory) -----------

    async def add_episode(self, text: str) -> int:
        text = text.strip()[:1200]
        (vec,) = await self.embed([text], "document")
        dim = len(vec)
        if self.get("episode_dim") not in (None, dim):
            self.db.execute("DROP TABLE IF EXISTS episode_vec")
        self.db.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS episode_vec USING vec0(embedding float[{dim}] distance_metric=cosine)"
        )
        self.put("episode_dim", dim)
        eid = self.db.execute("INSERT INTO episodes(text, ts) VALUES (?, ?)", (text, time.time())).lastrowid
        self.db.execute("INSERT INTO episode_vec(rowid, embedding) VALUES (?, ?)",
                        (eid, sqlite_vec.serialize_float32(vec)))
        self.db.commit()
        return eid

    async def recall_episodes(self, query: str, k: int = 4, before: float | None = None,
                              max_distance: float = 0.7) -> list[Fact]:
        """Past exchanges related to `query`, oldest first. `before`: only ones older than this time
        (the recent ones are already in the chat history)."""
        if not self._has_vec("episode_vec") or not query.strip():
            return []
        (vec,) = await self.embed([query], "query")
        rows = self.db.execute(
            """SELECT e.id, e.text, e.ts, v.distance
               FROM (SELECT rowid, distance FROM episode_vec WHERE embedding MATCH ? AND k = ?) v
               JOIN episodes e ON e.id = v.rowid ORDER BY v.distance""",
            (sqlite_vec.serialize_float32(vec), k * 3),
        ).fetchall()
        hits = [Fact(*r) for r in rows if r[3] <= max_distance and (before is None or r[2] < before)][:k]
        return sorted(hits, key=lambda f: f.ts)

    def oldest_recent_message_time(self, n: int) -> float | None:
        row = self.db.execute("SELECT MIN(ts) FROM (SELECT ts FROM messages ORDER BY id DESC LIMIT ?)", (n,)).fetchone()
        return row[0] if row else None

    def forget_everything(self) -> None:
        self.db.executescript("DELETE FROM messages; DELETE FROM facts; DELETE FROM kv; DELETE FROM episodes;")
        for table in ("fact_vec", "episode_vec"):
            if self._has_vec(table):
                self.db.execute(f"DROP TABLE {table}")
        self.db.commit()

    def close(self) -> None:
        self.db.close()
