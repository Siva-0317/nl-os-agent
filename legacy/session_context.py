# session_context.py
"""
Session Context Manager for NL OS Agent.
Maintains conversational state, multi-turn history, directory tracking,
and entity reference resolution (e.g. 'that file', 'it', 'the folder') using SQLite.
"""

import os
import re
import sqlite3
import shlex
from datetime import datetime


class SessionContextManager:
    def __init__(self, session_id: str, db_path: str = "session.db"):
        self.session_id = session_id
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_db()
        self.cwd = os.path.abspath(os.getcwd())
        self.turn = 0

    def _init_db(self):
        """Create database tables if they do not exist."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS commands (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                turn INTEGER,
                nl_request TEXT,
                command TEXT,
                risk_tier TEXT,
                output TEXT,
                timestamp TEXT
            );
            CREATE TABLE IF NOT EXISTS entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                name TEXT,
                entity_type TEXT,
                full_path TEXT,
                last_turn INTEGER
            );
        """)
        self.conn.commit()

    def resolve(self, nl_request: str) -> str:
        """
        Enrich NL request with session context and resolve reference pronouns.
        """
        self.turn += 1
        context_parts = [f"Current directory: {self.cwd}"]

        # Add up to 3 recent commands from history
        cursor = self.conn.execute("""
            SELECT command, output FROM commands
            WHERE session_id=? ORDER BY turn DESC LIMIT 3
        """, (self.session_id,))
        recent = cursor.fetchall()

        if recent:
            history_str = "; ".join([f"ran '{r[0]}'" for r in reversed(recent)])
            context_parts.append(f"Recent commands: {history_str}")

        # Resolve entity references (e.g. 'that file', 'that folder', 'it')
        resolved = self._resolve_references(nl_request)
        context = " | ".join(context_parts)
        return f"Context: [{context}]\nRequest: {resolved}"

    def _resolve_references(self, text: str) -> str:
        """Replace references like 'that file', 'it', 'the folder' with actual paths."""
        reference_patterns = [
            (r"\bthat folder\b", "dir"),
            (r"\bthe folder\b", "dir"),
            (r"\bthat directory\b", "dir"),
            (r"\bthe directory\b", "dir"),
            (r"\bthat script\b", "file"),
            (r"\bthe script\b", "file"),
            (r"\bthat file\b", "file"),
            (r"\bthe file\b", "file"),
            (r"\bit\b", None),
        ]

        for pattern, ent_type in reference_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                entity = self._lookup_latest_entity(ent_type)
                if entity:
                    text = re.sub(pattern, entity, text, flags=re.IGNORECASE)
        return text

    def _lookup_latest_entity(self, entity_type: str | None = None) -> str | None:
        """Get the most recently referenced file or directory."""
        if entity_type:
            row = self.conn.execute("""
                SELECT full_path FROM entities
                WHERE session_id=? AND entity_type=?
                ORDER BY last_turn DESC, id DESC LIMIT 1
            """, (self.session_id, entity_type)).fetchone()
            if row:
                return row[0]

        # Fallback to any latest entity
        row = self.conn.execute("""
            SELECT full_path FROM entities
            WHERE session_id=? ORDER BY last_turn DESC, id DESC LIMIT 1
        """, (self.session_id,)).fetchone()
        return row[0] if row else None

    def update(self, nl_request: str, command: str, risk_tier: str, output: str):
        """Save executed command to history and extract new entity references."""
        self.conn.execute("""
            INSERT INTO commands VALUES
            (NULL, ?, ?, ?, ?, ?, ?, ?)
        """, (
            self.session_id, self.turn, nl_request,
            command, risk_tier, (output or "")[:500],
            datetime.now().isoformat()
        ))
        self.conn.commit()

        # Update CWD if 'cd' command
        cmd_stripped = command.strip()
        if cmd_stripped.startswith("cd ") or cmd_stripped == "cd":
            target = cmd_stripped[3:].strip() if len(cmd_stripped) > 2 else ""
            if not target or target == "~":
                self.cwd = os.path.expanduser("~")
            elif target == "..":
                self.cwd = os.path.dirname(self.cwd)
            else:
                new_path = os.path.normpath(os.path.join(self.cwd, target))
                if os.path.exists(new_path) and os.path.isdir(new_path):
                    self.cwd = new_path
                else:
                    self.cwd = new_path

        # Extract referenced entities from command
        self._extract_entities(command)

    def _extract_entities(self, command: str):
        """Extract file and directory paths from a shell command."""
        try:
            tokens = shlex.split(command.strip())
        except Exception:
            tokens = command.strip().split()

        if not tokens:
            return

        base_cmd = tokens[0].split("/")[-1].split("\\")[-1]

        for token in tokens[1:]:
            # Skip flags
            if token.startswith("-"):
                continue
            # Skip operators
            if token in (">", ">>", "|", "&&", ";"):
                continue

            cleaned_token = token.strip("\'\"`")
            if not cleaned_token or cleaned_token in (".", "..", "/"):
                continue

            name = os.path.basename(cleaned_token.rstrip("/\\"))
            if not name:
                continue

            # Determine entity type
            if base_cmd in ("mkdir", "rmdir", "cd"):
                entity_type = "dir"
            elif "." in name:
                entity_type = "file"
            elif cleaned_token.endswith("/") or cleaned_token.endswith("\\"):
                entity_type = "dir"
            else:
                entity_type = "dir" if "folder" in command or "dir" in command else "file"

            self.conn.execute("""
                INSERT INTO entities (session_id, name, entity_type, full_path, last_turn)
                VALUES (?, ?, ?, ?, ?)
            """, (self.session_id, name, entity_type, cleaned_token, self.turn))

        self.conn.commit()

    def get_history_summary(self) -> str:
        """Return human-readable session summary of commands executed."""
        rows = self.conn.execute("""
            SELECT turn, command, risk_tier FROM commands
            WHERE session_id=? ORDER BY turn
        """, (self.session_id,)).fetchall()

        if not rows:
            return "No commands run yet."
        lines = [f"Turn {r[0]}: [{r[2]}] {r[1]}" for r in rows]
        return "\n".join(lines)


if __name__ == "__main__":
    import uuid
    test_session = str(uuid.uuid4())
    ctx = SessionContextManager(test_session, db_path=":memory:")

    # Turn 1
    resolved_1 = ctx.resolve("list all files here")
    print(f"Resolved T1: {resolved_1}\n")
    ctx.update("list all files here", "ls -la", "read_only", "file1.txt\nfile2.py")

    # Turn 2 — create folder
    resolved_2 = ctx.resolve("create a folder called backup")
    print(f"Resolved T2: {resolved_2}\n")
    ctx.update("create a folder called backup", "mkdir backup", "state_modifying", "")

    # Turn 3 — reference resolution
    resolved_3 = ctx.resolve("copy all python files into that folder")
    print(f"Resolved T3: {resolved_3}\n")
    ctx.update("copy all python files into that folder", "cp *.py backup", "state_modifying", "")

    # Turn 4 — history summary
    print("History:")
    print(ctx.get_history_summary())
