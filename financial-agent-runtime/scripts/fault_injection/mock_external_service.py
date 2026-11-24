"""Independent Mock External Service with durable side-effect counter.

Runs as its own process/container. Persists to a SQLite file volume so the
actual business side-effect count survives worker/container restarts.
Never shares the application PostgreSQL database.
"""
from __future__ import annotations

import asyncio
import sqlite3
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel

DB_PATH = Path("/app/fi-data/external_effects.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
_lock = threading.Lock()


def _init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS effects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                idempotency_key TEXT NOT NULL UNIQUE,
                tenant_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                operation TEXT NOT NULL,
                payload TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS ix_effects_run ON effects(run_id)")
        conn.commit()


_init_db()


class EffectRequest(BaseModel):
    idempotency_key: str
    tenant_id: str
    run_id: str
    operation: str = "write"
    payload: str = ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="Finora FI Mock External Service", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "db": str(DB_PATH)}


@app.post("/effects")
async def create_effect(body: EffectRequest):
    """Idempotent business side effect. Returns duplicate=True if key already persisted."""
    def _write():
        with _lock, sqlite3.connect(DB_PATH, timeout=10) as conn:
            try:
                cur = conn.execute(
                    "INSERT INTO effects(idempotency_key, tenant_id, run_id, operation, payload) VALUES (?,?,?,?,?)",
                    (body.idempotency_key, body.tenant_id, body.run_id, body.operation, body.payload),
                )
                conn.commit()
                return {"duplicate": False, "effect_id": cur.lastrowid}
            except sqlite3.IntegrityError:
                return {"duplicate": True, "effect_id": None}
    return await asyncio.to_thread(_write)


@app.get("/effects/count")
async def effect_count(run_id: str, tenant_id: str | None = None):
    def _read():
        with sqlite3.connect(DB_PATH) as conn:
            if tenant_id:
                row = conn.execute("SELECT COUNT(*) FROM effects WHERE run_id=? AND tenant_id=?", (run_id, tenant_id)).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) FROM effects WHERE run_id=?", (run_id,)).fetchone()
            return {"run_id": run_id, "count": row[0]}
    return await asyncio.to_thread(_read)


@app.get("/effects")
async def list_effects(run_id: str):
    def _read():
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT idempotency_key, tenant_id, operation, created_at FROM effects WHERE run_id=? ORDER BY id",
                (run_id,),
            ).fetchall()
            return [{"idempotency_key": r[0], "tenant_id": r[1], "operation": r[2], "created_at": r[3]} for r in rows]
    return await asyncio.to_thread(_read)


@app.post("/effects/reset")
async def reset_effects(run_id: str):
    def _delete():
        with _lock, sqlite3.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM effects WHERE run_id=?", (run_id,))
            conn.commit()
    await asyncio.to_thread(_delete)
    return {"run_id": run_id, "reset": True}
