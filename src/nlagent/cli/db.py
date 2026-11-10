"""
db.py - SQLite Persistence for NL OS Agent Proposals, State, and Quarantine.

Stores and queries:
1. Proposed commands, risk tiers, previews, and status ('proposed', 'committed', 'undone', 'blocked').
2. Linked quarantine IDs for rollback tracking.
3. Turn histories and executions across terminal sessions.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def get_default_db_path(override_path: str | Path | None = None) -> Path:
    """Determine path to local SQLite state database."""
    if override_path:
        p = Path(override_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    if os.environ.get("NLAGENT_STATE_DB"):
        p = Path(os.environ["NLAGENT_STATE_DB"])
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    candidates: list[Path] = []
    if os.environ.get("NLAGENT_STATE_DIR"):
        candidates.append(Path(os.environ["NLAGENT_STATE_DIR"]) / "state.db")

    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        candidates.append(Path(os.environ["LOCALAPPDATA"]) / "NLAgent" / "state.db")

    candidates.append(Path.home() / ".nl-agent" / "state.db")
    candidates.append(Path(tempfile.gettempdir()) / ".nl-agent" / "state.db")

    for cand in candidates:
        try:
            cand.parent.mkdir(parents=True, exist_ok=True)
            test_file = cand.parent / ".perm_check"
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink(missing_ok=True)
            return cand
        except Exception:
            continue

    temp_candidate = Path(tempfile.gettempdir()) / ".nl-agent" / "state.db"
    temp_candidate.parent.mkdir(parents=True, exist_ok=True)
    return temp_candidate


class SQLiteStateStore:
    """Thread-safe SQLite manager for nlctl state persistence."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = get_default_db_path(db_path)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create tables if they do not exist."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS proposals (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    cwd TEXT NOT NULL,
                    request TEXT NOT NULL,
                    command TEXT NOT NULL,
                    tier TEXT NOT NULL,
                    preview TEXT,
                    explanation TEXT,
                    targets TEXT,
                    status TEXT DEFAULT 'proposed',
                    quarantine_id TEXT,
                    exit_code INTEGER,
                    output TEXT
                );
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals (status);
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_proposals_time ON proposals (timestamp);
            """)

    def save_proposal(
        self,
        proposal_id: str,
        request: str,
        command: str,
        tier: str,
        cwd: str,
        preview_text: str = "",
        explanation: str = "",
        targets: list[str] | None = None,
        quarantine_id: str | None = None,
        status: str = "proposed",
    ) -> str:
        """Persist a new proposal record."""
        ts = datetime.now(timezone.utc).isoformat()
        targets_json = json.dumps(targets or [])

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO proposals (
                    id, timestamp, cwd, request, command, tier,
                    preview, explanation, targets, status, quarantine_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    proposal_id,
                    ts,
                    cwd,
                    request,
                    command,
                    tier,
                    preview_text,
                    explanation,
                    targets_json,
                    status,
                    quarantine_id,
                ),
            )
        return proposal_id

    def get_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        """Retrieve proposal record by ID."""
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM proposals WHERE id = ?;", (proposal_id,))
            row = cur.fetchone()
            if not row:
                return None
            res = dict(row)
            try:
                res["targets"] = json.loads(res.get("targets") or "[]")
            except Exception:
                res["targets"] = []
            return res

    def update_proposal_status(
        self,
        proposal_id: str,
        status: str,
        exit_code: int | None = None,
        output: str | None = None,
        quarantine_id: str | None = None,
    ) -> bool:
        """Update execution outcome for a proposal."""
        with self._get_connection() as conn:
            updates = ["status = ?"]
            params: list[Any] = [status]

            if exit_code is not None:
                updates.append("exit_code = ?")
                params.append(exit_code)
            if output is not None:
                updates.append("output = ?")
                params.append(output)
            if quarantine_id is not None:
                updates.append("quarantine_id = ?")
                params.append(quarantine_id)

            params.append(proposal_id)
            sql = f"UPDATE proposals SET {', '.join(updates)} WHERE id = ?;"
            cur = conn.execute(sql, tuple(params))
            return cur.rowcount > 0

    def get_latest_proposal(self, status: str | None = None) -> dict[str, Any] | None:
        """Get the most recent proposal matching optional status."""
        with self._get_connection() as conn:
            if status:
                cur = conn.execute(
                    "SELECT * FROM proposals WHERE status = ? ORDER BY timestamp DESC LIMIT 1;",
                    (status,),
                )
            else:
                cur = conn.execute(
                    "SELECT * FROM proposals ORDER BY timestamp DESC LIMIT 1;"
                )
            row = cur.fetchone()
            if not row:
                return None
            res = dict(row)
            try:
                res["targets"] = json.loads(res.get("targets") or "[]")
            except Exception:
                res["targets"] = []
            return res

    def list_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """List historical proposals ordered by timestamp descending."""
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM proposals ORDER BY timestamp DESC LIMIT ?;",
                (limit,),
            )
            rows = cur.fetchall()
            history = []
            for r in rows:
                d = dict(r)
                try:
                    d["targets"] = json.loads(d.get("targets") or "[]")
                except Exception:
                    d["targets"] = []
                history.append(d)
            return history

