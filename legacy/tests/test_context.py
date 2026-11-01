# tests/test_context.py
"""
Unit tests for Session Context Manager and Reference Resolution.
"""

import sys
import os
import uuid

# Add parent directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from session_context import SessionContextManager


class TestSessionContext:
    @pytest.fixture
    def ctx(self):
        """Create an in-memory session manager for testing."""
        session_id = str(uuid.uuid4())
        return SessionContextManager(session_id=session_id, db_path=":memory:")

    def test_init_db(self, ctx):
        assert ctx.turn == 0
        assert os.path.exists(ctx.cwd)

    def test_context_enrichment(self, ctx):
        ctx.update("show files", "ls -la", "read_only", "file1.txt")
        resolved = ctx.resolve("show again")
        assert "Current directory:" in resolved
        assert "Recent commands: ran 'ls -la'" in resolved
        assert "Request: show again" in resolved

    def test_reference_resolution_folder(self, ctx):
        # Turn 1: create backup folder
        ctx.resolve("create backup folder")
        ctx.update("create backup folder", "mkdir backup_dir", "state_modifying", "")

        # Turn 2: refer to that folder
        resolved = ctx.resolve("copy files into that folder")
        assert "backup_dir" in resolved

    def test_reference_resolution_file(self, ctx):
        # Turn 1: touch a script
        ctx.resolve("create test script")
        ctx.update("create test script", "touch test_script.py", "state_modifying", "")

        # Turn 2: refer to that script
        resolved = ctx.resolve("run that script")
        assert "test_script.py" in resolved

    def test_cd_updates_cwd(self, ctx):
        initial_cwd = ctx.cwd
        ctx.update("move up one level", "cd ..", "state_modifying", "")
        expected = os.path.dirname(initial_cwd)
        assert ctx.cwd == expected

    def test_history_summary(self, ctx):
        ctx.update("list", "ls", "read_only", "files")
        ctx.update("make dir", "mkdir test", "state_modifying", "")
        summary = ctx.get_history_summary()
        assert "Turn 0: [read_only] ls" in summary
        assert "Turn 0: [state_modifying] mkdir test" in summary
