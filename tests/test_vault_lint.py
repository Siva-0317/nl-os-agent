"""
tests/test_vault_lint.py - Unit tests for Vault Integrity Linter (`scripts/vault_lint.py`).

Verifies:
1. Clean valid vault passes with zero violations.
2. Structural check detects missing governance files (AGENTS.md, hot.md, index.md, log.md).
3. Orphan page detection flags unlinked pages not cataloged in index.md.
4. Missing log entry check flags pages not recorded in log.md.
5. Unlinked number check flags quantitative metrics lacking links while allowing linked ones.
6. Command-line interface returns correct status codes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.vault_lint import VaultLinter, main


@pytest.fixture
def sample_vault(tmp_path: Path) -> Path:
    """Create a minimal valid sample vault in a temporary directory."""
    vault = tmp_path / "test_vault"
    vault.mkdir()
    wiki = vault / "wiki"
    wiki.mkdir()
    raw = vault / "raw"
    raw.mkdir()
    decisions = wiki / "decisions"
    decisions.mkdir()

    # 1. AGENTS.md
    (vault / "AGENTS.md").write_text(
        "# AGENTS.md\nRead hot.md then index.md. Never copy numbers. Log changes. Never edit raw/.\n",
        encoding="utf-8",
    )

    # 2. hot.md
    (wiki / "hot.md").write_text("# Hot Context\nActive task: Testing.\n", encoding="utf-8")

    # 3. Decision D01 page (clean with linked number)
    (decisions / "D01.md").write_text(
        "# D01 Decision\n"
        "We chose the fast path with [98.5% accuracy](../../results/test.jsonl).\n",
        encoding="utf-8",
    )

    # 4. index.md
    (wiki / "index.md").write_text(
        "# Master Index\n"
        "- [D01 Decision](decisions/D01.md)\n",
        encoding="utf-8",
    )

    # 5. log.md
    (wiki / "log.md").write_text(
        "# Log\n"
        "## 2026-11-09\n"
        "- Created D01.md\n",
        encoding="utf-8",
    )

    return vault


class TestVaultLinter:
    """Unit test suite for VaultLinter rules."""

    def test_clean_sample_vault_passes(self, sample_vault: Path):
        linter = VaultLinter(vault_dir=sample_vault)
        violations = linter.run_all()
        assert len(violations) == 0

    def test_missing_structure_detected(self, sample_vault: Path):
        # Remove AGENTS.md
        (sample_vault / "AGENTS.md").unlink()

        linter = VaultLinter(vault_dir=sample_vault)
        violations = linter.run_all()
        assert any(v.category == "Structure" and "AGENTS.md" in v.message for v in violations)

    def test_orphan_page_detected(self, sample_vault: Path):
        # Add an uncataloged decision page
        orphan = sample_vault / "wiki" / "decisions" / "D02_orphan.md"
        orphan.write_text("# D02 Orphan\nSome note.\n", encoding="utf-8")

        # Also add it to log so only the orphan check fails
        log_file = sample_vault / "wiki" / "log.md"
        log_file.write_text(log_file.read_text(encoding="utf-8") + "- D02_orphan.md\n", encoding="utf-8")

        linter = VaultLinter(vault_dir=sample_vault)
        violations = linter.run_all()
        assert any(v.category == "Orphan Page" and "D02_orphan.md" in v.message for v in violations)

    def test_missing_log_entry_detected(self, sample_vault: Path):
        # Add page to index, but do not log it in log.md
        new_page = sample_vault / "wiki" / "decisions" / "D03_unlogged.md"
        new_page.write_text("# D03\nUnlogged content.\n", encoding="utf-8")

        index_file = sample_vault / "wiki" / "index.md"
        index_file.write_text(index_file.read_text(encoding="utf-8") + "- [D03](decisions/D03_unlogged.md)\n", encoding="utf-8")

        linter = VaultLinter(vault_dir=sample_vault)
        violations = linter.run_all()
        assert any(v.category == "Missing Log Entry" and "D03_unlogged.md" in v.message for v in violations)

    def test_unlinked_number_detected(self, sample_vault: Path):
        # Add page with bare unlinked percentage
        bad_page = sample_vault / "wiki" / "decisions" / "D04_bad_numbers.md"
        bad_page.write_text(
            "# D04\n"
            "This model achieved 95.5% accuracy in 14.2 ms latency without any citations.\n",
            encoding="utf-8",
        )

        index_file = sample_vault / "wiki" / "index.md"
        index_file.write_text(index_file.read_text(encoding="utf-8") + "- [D04](decisions/D04_bad_numbers.md)\n", encoding="utf-8")

        log_file = sample_vault / "wiki" / "log.md"
        log_file.write_text(log_file.read_text(encoding="utf-8") + "- D04_bad_numbers.md\n", encoding="utf-8")

        linter = VaultLinter(vault_dir=sample_vault)
        violations = linter.run_all()

        unlinked = [v for v in violations if v.category == "Unlinked Number"]
        assert len(unlinked) >= 1
        assert "D04_bad_numbers.md" in str(unlinked[0].file_path)
        assert unlinked[0].line_number == 2

    def test_linked_number_passes(self, sample_vault: Path):
        # Add page with properly linked numbers
        good_page = sample_vault / "wiki" / "decisions" / "D05_good_numbers.md"
        good_page.write_text(
            "# D05\n"
            "Achieved [95.5% accuracy](../../results/bench.jsonl) with [14.2 ms latency](../../results/latency.jsonl).\n",
            encoding="utf-8",
        )

        index_file = sample_vault / "wiki" / "index.md"
        index_file.write_text(index_file.read_text(encoding="utf-8") + "- [D05](decisions/D05_good_numbers.md)\n", encoding="utf-8")

        log_file = sample_vault / "wiki" / "log.md"
        log_file.write_text(log_file.read_text(encoding="utf-8") + "- D05_good_numbers.md\n", encoding="utf-8")

        linter = VaultLinter(vault_dir=sample_vault)
        violations = linter.run_all()
        assert len(violations) == 0

    def test_cli_execution_clean(self, sample_vault: Path, capsys):
        code = main(["--vault-dir", str(sample_vault)])
        assert code == 0
        captured = capsys.readouterr().out
        assert "[PASSED]" in captured

    def test_cli_execution_failure(self, sample_vault: Path, capsys):
        (sample_vault / "AGENTS.md").unlink()
        code = main(["--vault-dir", str(sample_vault)])
        assert code == 1
        captured = capsys.readouterr().out
        assert "[FAILED]" in captured

