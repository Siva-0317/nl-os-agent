"""
tests/test_renderer.py - Comprehensive Unit Tests for Deterministic Plain-English Renderer.

Verifies:
1. Target counts, human-readable bytes, and directory scope summarization.
2. Specific overwrite warnings and reversibility/quarantine statuses.
3. Mandatory safety policy: Cancel MUST be designated as default action for destructive tiers.
4. Edge cases:
   - Wildcards (*.log, *.tmp)
   - Drive roots (/, C:\\, D:\\)
   - Bulk deletions (large counts and high byte volumes)
"""

from __future__ import annotations

import os
from pathlib import Path
import pytest

from nlagent.safety.effects import Effect
from nlagent.safety.renderer import (
    EffectSummary,
    ExecutionPlan,
    format_bytes_human,
    is_root_or_drive_path,
    render_full_preview,
    render_plain_english_consequence,
    render_reversibility_description,
    summarize_directory_scopes,
)


# =============================================================================
# 1. Byte and Scope Formatting Helpers
# =============================================================================

def test_format_bytes_human():
    assert format_bytes_human(0) == "0 B"
    assert format_bytes_human(512) == "512 B"
    assert format_bytes_human(1536) == "1.5 KB"
    assert format_bytes_human(18 * 1024 * 1024) == "18.0 MB"
    assert format_bytes_human(int(1.5 * 1024 * 1024 * 1024)) == "1.50 GB"


def test_is_root_or_drive_path():
    assert is_root_or_drive_path("/")
    assert is_root_or_drive_path("C:")
    assert is_root_or_drive_path("C:\\")
    assert is_root_or_drive_path("D:/")
    assert is_root_or_drive_path("c:/")
    assert not is_root_or_drive_path("C:/Users/sivab/Downloads")
    assert not is_root_or_drive_path("/home/user/project")


def test_summarize_directory_scopes(tmp_path: Path):
    cwd = str(tmp_path)
    file1 = str(tmp_path / "Downloads" / "report.pdf")
    file2 = str(tmp_path / "Downloads" / "data.csv")
    file3 = str(tmp_path / "src" / "main.py")

    scopes = summarize_directory_scopes([file1, file2, file3], cwd=cwd)
    assert any("Downloads" in s for s in scopes)
    assert any("src" in s for s in scopes)


# =============================================================================
# 2. Plain-English Consequence Rendering
# =============================================================================

def test_render_read_only_command():
    eff = Effect()
    plan = ExecutionPlan.create(command="ls -la", effect=eff)
    assert plan.tier == "read_only"
    assert "Read-only" in plan.plain_english_description
    assert plan.default_action == "execute"
    assert "Enter to run" in plan.confirmation_prompt


def test_render_single_file_deletion():
    eff = Effect(deletes=["/tmp/test_dir/junk.log"], bytes_affected=2048)
    plan = ExecutionPlan.create(command="rm /tmp/test_dir/junk.log", effect=eff, cwd="/tmp/test_dir")
    assert plan.tier == "destructive"
    assert "junk.log" in plan.plain_english_description
    assert "2.0 KB" in plan.plain_english_description
    assert plan.default_action == "cancel"
    assert plan.effect_summary.is_reversible


def test_render_multiple_file_deletion():
    eff = Effect(
        deletes=[
            "/workspace/downloads/file1.zip",
            "/workspace/downloads/file2.zip",
            "/workspace/downloads/file3.zip",
        ],
        bytes_affected=15 * 1024 * 1024,
    )
    plan = ExecutionPlan.create(
        command="rm downloads/*.zip",
        effect=eff,
        cwd="/workspace",
    )
    assert "3 files" in plan.plain_english_description
    assert "15.0 MB" in plan.plain_english_description
    assert "wildcard" in plan.plain_english_description
    assert plan.default_action == "cancel"


def test_render_overwrite_warning():
    eff = Effect(overwrites=["/workspace/config.json"])
    plan = ExecutionPlan.create(command="echo '{}' > config.json", effect=eff, cwd="/workspace")
    assert plan.tier == "destructive"
    assert "overwrite 'config.json'" in plan.plain_english_description
    assert "replaced" in plan.plain_english_description
    assert plan.default_action == "cancel"


def test_render_creates_and_appends():
    eff = Effect(creates=["/workspace/new.txt"], appends=["/workspace/log.txt"])
    plan = ExecutionPlan.create(command="touch new.txt && echo hi >> log.txt", effect=eff, cwd="/workspace")
    assert plan.tier == "state_modifying"
    assert "create 1 new item" in plan.plain_english_description
    assert "append data to 1 file" in plan.plain_english_description
    assert plan.default_action == "execute"


def test_render_process_signal_termination():
    eff = Effect(signals=["pid:1234", "pid:5678"])
    plan = ExecutionPlan.create(command="kill -9 1234 5678", effect=eff)
    assert plan.tier == "destructive"
    assert "terminate running system processes" in plan.plain_english_description
    assert plan.default_action == "cancel"


# =============================================================================
# 3. Default Action & Reversibility Policies
# =============================================================================

def test_destructive_tier_mandates_cancel_default():
    """Cancel MUST be designated as the default action for destructive tiers."""
    eff = Effect(deletes=["/home/user/document.docx"], bytes_affected=1000)
    plan = ExecutionPlan.create(command="rm document.docx", effect=eff)

    assert plan.tier == "destructive"
    assert plan.default_action == "cancel"
    assert "Cancel is default" in plan.confirmation_prompt


def test_reversibility_status_with_quarantine_enabled():
    eff = Effect(deletes=["/workspace/temp.txt"], bytes_affected=1024)
    plan = ExecutionPlan.create(command="rm temp.txt", effect=eff, enable_quarantine=True)
    assert plan.effect_summary.is_reversible
    assert "Protected by shadow quarantine" in plan.reversibility_description
    assert "nlctl undo" in plan.reversibility_description


def test_reversibility_status_with_quarantine_disabled():
    eff = Effect(deletes=["/workspace/temp.txt"], bytes_affected=1024)
    plan = ExecutionPlan.create(command="rm temp.txt", effect=eff, enable_quarantine=False)
    assert not plan.effect_summary.is_reversible
    assert "IRREVERSIBLE" in plan.reversibility_description


def test_reversibility_exceeds_quarantine_limit():
    # Payload > 500 MB exceeds quarantine safety ceiling
    giant_bytes = 600 * 1024 * 1024
    eff = Effect(deletes=["/workspace/giant.vmdk"], bytes_affected=giant_bytes)
    plan = ExecutionPlan.create(command="rm giant.vmdk", effect=eff, enable_quarantine=True)
    assert not plan.effect_summary.is_reversible
    assert "500 MB" in plan.reversibility_description


# =============================================================================
# 4. Edge Cases: Wildcards, Drive Roots, Bulk Deletions
# =============================================================================

def test_edge_case_wildcards():
    eff = Effect(
        deletes=[f"/var/log/app_{i}.log" for i in range(5)],
        bytes_affected=50000,
    )
    plan = ExecutionPlan.create(command="rm -rf /var/log/*.log", effect=eff)
    assert plan.effect_summary.has_wildcards
    assert "matching wildcard pattern" in plan.plain_english_description


def test_edge_case_drive_root_deletion():
    # Unix root
    eff_unix = Effect(deletes=["/"], bytes_affected=1000000, protected_hit=True)
    plan_unix = ExecutionPlan.create(command="rm -rf /", effect=eff_unix)
    assert plan_unix.effect_summary.is_drive_root
    assert "CRITICAL" in plan_unix.plain_english_description
    assert plan_unix.default_action == "cancel"

    # Windows root C:\
    eff_win = Effect(deletes=["C:\\"], bytes_affected=5000000, protected_hit=True)
    plan_win = ExecutionPlan.create(command="rd /s /q C:\\", effect=eff_win)
    assert plan_win.effect_summary.is_drive_root
    assert "CRITICAL" in plan_win.plain_english_description
    assert plan_win.default_action == "cancel"


def test_edge_case_bulk_deletions():
    # 60 items (> 25 threshold)
    many_files = [f"/workspace/cache/item_{i}.bin" for i in range(60)]
    eff = Effect(deletes=many_files, bytes_affected=60 * 1024 * 1024)
    plan = ExecutionPlan.create(command="rm -rf /workspace/cache/*", effect=eff, cwd="/workspace")
    assert plan.effect_summary.is_bulk
    assert "BULK DELETION" in plan.plain_english_description
    assert "60 items" in plan.plain_english_description
    assert plan.default_action == "cancel"


def test_edge_case_hard_block_raw_disk():
    eff = Effect(hard_block=True)
    plan = ExecutionPlan.create(command="format C: /FS:NTFS /Q", effect=eff)
    assert plan.tier == "privileged"
    assert "BLOCKED" in plan.plain_english_description
    assert "Raw disk" in plan.plain_english_description or "partition" in plan.plain_english_description
    assert plan.default_action == "cancel"


# =============================================================================
# 5. Full Preview Terminal Presentation
# =============================================================================

def test_render_full_preview():
    eff = Effect(deletes=["/workspace/tmp/dump.sql"], bytes_affected=10 * 1024 * 1024)
    plan = ExecutionPlan.create(
        command="rm tmp/dump.sql",
        effect=eff,
        cwd="/workspace",
        explanation="Clean up database dump to free disk space",
        enable_quarantine=True,
    )

    preview_banner = render_full_preview(plan)
    assert "Risk Level    : [DESTRUCTIVE]" in preview_banner
    assert "Consequence   :" in preview_banner
    assert "Reversibility : Protected by shadow quarantine" in preview_banner
    assert "Default Action: [CANCEL]" in preview_banner
    assert "Rationale     : Clean up database dump" in preview_banner

    # Check to_dict serialization
    d = plan.to_dict()
    assert d["command"] == "rm tmp/dump.sql"
    assert d["tier"] == "destructive"
    assert d["default_action"] == "cancel"
    assert d["bytes_affected_human"] == "10.0 MB"

