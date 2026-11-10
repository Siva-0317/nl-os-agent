"""tests/test_effects_windows.py - Unit tests for Windows shell coverage and effect models.

Tests models for Remove-Item, del, rd, Move-Item, Copy-Item, robocopy /MIR,
Set-Content, Out-File, Clear-Content, registry writes, Stop-Process, taskkill,
format/diskpart (hard block), Invoke-Expression, -EncodedCommand and Start-Process -Verb RunAs.

Strict invariant:
All fixtures are created in isolated temporary directories.
Tests ONLY call `analyse` and never execute any command.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import pytest

_src = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _src not in sys.path:
    sys.path.insert(0, _src)

from nlagent.safety.cmd_parser import parse_cmd_line
from nlagent.safety.effects import Effect, analyse, preview, tier_from_effect
from nlagent.safety.ps_parser import parse_powershell


@pytest.fixture
def win_temp_workspace():
    """Create a temporary sandbox filesystem with dummy files and subfolders."""
    d = tempfile.mkdtemp(prefix="win_test_")
    src_dir = os.path.join(d, "src_data")
    dst_dir = os.path.join(d, "dst_data")
    os.makedirs(src_dir, exist_ok=True)
    os.makedirs(dst_dir, exist_ok=True)

    with open(os.path.join(src_dir, "sample.txt"), "w", encoding="utf-8") as f:
        f.write("A" * 2048)
    with open(os.path.join(src_dir, "archive.log"), "w", encoding="utf-8") as f:
        f.write("B" * 1024)
    with open(os.path.join(dst_dir, "existing.txt"), "w", encoding="utf-8") as f:
        f.write("C" * 512)

    yield {
        "root": d,
        "src_dir": src_dir,
        "dst_dir": dst_dir,
        "sample": os.path.join(src_dir, "sample.txt"),
        "archive": os.path.join(src_dir, "archive.log"),
        "existing": os.path.join(dst_dir, "existing.txt"),
    }
    shutil.rmtree(d, ignore_errors=True)


class TestWindowsPowerShellModels:
    """Test PowerShell cmdlets and aliases via effects.analyse."""

    def test_remove_item_file_and_recurse(self, win_temp_workspace):
        ws = win_temp_workspace
        # Single file deletion
        eff_file = analyse(f'Remove-Item -Path "{ws["sample"]}" -Force', cwd=ws["root"])
        assert tier_from_effect(eff_file) == "destructive"
        assert ws["sample"] in eff_file.deletes
        assert eff_file.bytes_affected >= 2048

        # Directory recursion with alias ri
        eff_dir = analyse(f'ri -Path "{ws["src_dir"]}" -Recurse -Force', cwd=ws["root"])
        assert tier_from_effect(eff_dir) == "destructive"
        assert any(ws["sample"] in d for d in eff_dir.deletes)
        assert any(ws["archive"] in d for d in eff_dir.deletes)
        assert eff_dir.bytes_affected >= 3072

    def test_move_item(self, win_temp_workspace):
        ws = win_temp_workspace
        # Move existing file to destination
        target_dst = os.path.join(ws["dst_dir"], "sample_moved.txt")
        eff = analyse(f'Move-Item -Path "{ws["sample"]}" -Destination "{target_dst}"', cwd=ws["root"])
        assert tier_from_effect(eff) == "destructive"
        assert ws["sample"] in eff.deletes
        assert target_dst in eff.creates

    def test_copy_item(self, win_temp_workspace):
        ws = win_temp_workspace
        # Copy file to new target (creates)
        new_copy = os.path.join(ws["dst_dir"], "copy.txt")
        eff = analyse(f'Copy-Item -Path "{ws["sample"]}" -Destination "{new_copy}"', cwd=ws["root"])
        assert tier_from_effect(eff) == "state_modifying"
        assert new_copy in eff.creates
        assert ws["sample"] not in eff.deletes

        # Copy over existing target (overwrites -> destructive)
        eff_ovw = analyse(f'cpi -Path "{ws["sample"]}" -Destination "{ws["existing"]}" -Force', cwd=ws["root"])
        assert tier_from_effect(eff_ovw) == "destructive"
        assert ws["existing"] in eff_ovw.overwrites

    def test_set_content(self, win_temp_workspace):
        ws = win_temp_workspace
        # Overwriting existing file
        eff = analyse(f'Set-Content -Path "{ws["sample"]}" -Value "New payload"', cwd=ws["root"])
        assert tier_from_effect(eff) == "destructive"
        assert ws["sample"] in eff.overwrites

        # Creating non-existent file
        new_file = os.path.join(ws["root"], "created.txt")
        eff_new = analyse(f'sc -Path "{new_file}" -Value "Hello"', cwd=ws["root"])
        assert tier_from_effect(eff_new) == "state_modifying"
        assert new_file in eff_new.creates

    def test_out_file_and_append(self, win_temp_workspace):
        ws = win_temp_workspace
        # Out-File overwrites
        eff_ovw = analyse(f'Out-File -FilePath "{ws["existing"]}"', cwd=ws["root"])
        assert tier_from_effect(eff_ovw) == "destructive"
        assert ws["existing"] in eff_ovw.overwrites

        # Out-File with -Append
        eff_app = analyse(f'Out-File -FilePath "{ws["existing"]}" -Append', cwd=ws["root"])
        assert tier_from_effect(eff_app) == "state_modifying"
        assert ws["existing"] in eff_app.appends

    def test_clear_content(self, win_temp_workspace):
        ws = win_temp_workspace
        # Clear-Content truncates file (overwrites to 0 bytes)
        eff = analyse(f'Clear-Content -Path "{ws["sample"]}"', cwd=ws["root"])
        assert tier_from_effect(eff) == "destructive"
        assert ws["sample"] in eff.overwrites

        # Alias clc
        eff_alias = analyse(f'clc -Path "{ws["archive"]}"', cwd=ws["root"])
        assert tier_from_effect(eff_alias) == "destructive"
        assert ws["archive"] in eff_alias.overwrites

    def test_stop_process_and_aliases(self, win_temp_workspace):
        ws = win_temp_workspace
        eff = analyse("Stop-Process -Name notepad", cwd=ws["root"])
        assert tier_from_effect(eff) == "destructive"
        assert "notepad" in eff.signals

        eff_spps = analyse("spps -Id 4321", cwd=ws["root"])
        assert tier_from_effect(eff_spps) == "destructive"
        assert "4321" in eff_spps.signals


class TestWindowsCmdModels:
    """Test cmd.exe verbs via effects.analyse."""

    def test_cmd_del_file_and_recurse(self, win_temp_workspace):
        ws = win_temp_workspace
        eff = analyse(f'del /f /q "{ws["sample"]}"', cwd=ws["root"])
        assert tier_from_effect(eff) == "destructive"
        assert ws["sample"] in eff.deletes
        assert eff.bytes_affected >= 2048

        eff_s = analyse(f'del /s /q "{ws["src_dir"]}\\*.log"', cwd=ws["root"])
        assert tier_from_effect(eff_s) == "destructive"
        assert ws["archive"] in eff_s.deletes

    def test_cmd_rd_rmdir(self, win_temp_workspace):
        ws = win_temp_workspace
        eff = analyse(f'rd /s /q "{ws["src_dir"]}"', cwd=ws["root"])
        assert tier_from_effect(eff) == "destructive"
        assert any(ws["sample"] in d for d in eff.deletes)
        assert any(ws["archive"] in d for d in eff.deletes)
        assert eff.bytes_affected >= 3072

    def test_robocopy_mir_vs_standard(self, win_temp_workspace):
        ws = win_temp_workspace
        # Standard copy is state_modifying or overwriting
        eff_std = analyse(f'robocopy "{ws["src_dir"]}" "{ws["dst_dir"]}" /E', cwd=ws["root"])
        assert "robocopy /MIR" not in "".join(eff_std.notes)

        # /MIR mirror purge is explicitly destructive
        eff_mir = analyse(f'robocopy "{ws["src_dir"]}" "{ws["dst_dir"]}" /MIR /R:0 /W:0', cwd=ws["root"])
        assert tier_from_effect(eff_mir) == "destructive"
        assert any("mirror directory tree" in n for n in eff_mir.notes)
        assert ws["existing"] in eff_mir.deletes or ws["existing"] in eff_mir.overwrites

    def test_cmd_move_and_copy(self, win_temp_workspace):
        ws = win_temp_workspace
        target_dst = os.path.join(ws["dst_dir"], "sample_moved2.txt")
        eff_mv = analyse(f'move /Y "{ws["sample"]}" "{target_dst}"', cwd=ws["root"])
        assert tier_from_effect(eff_mv) == "destructive"
        assert ws["sample"] in eff_mv.deletes
        assert target_dst in eff_mv.creates

        eff_cp = analyse(f'copy /Y "{ws["sample"]}" "{target_dst}"', cwd=ws["root"])
        assert tier_from_effect(eff_cp) == "state_modifying"
        assert target_dst in eff_cp.creates
        assert ws["sample"] not in eff_cp.deletes

    def test_cmd_taskkill(self, win_temp_workspace):
        ws = win_temp_workspace
        eff = analyse("taskkill /F /IM calc.exe", cwd=ws["root"])
        assert tier_from_effect(eff) == "destructive"
        assert "calc.exe" in eff.signals

        eff_pid = analyse("taskkill /PID 9999", cwd=ws["root"])
        assert tier_from_effect(eff_pid) == "destructive"
        assert "9999" in eff_pid.signals


class TestRegistryWritesAndPrivilege:
    """Test registry modifications and system privilege detection."""

    def test_powershell_hklm_registry_is_privileged(self, win_temp_workspace):
        ws = win_temp_workspace
        eff = analyse('Set-ItemProperty -Path "HKLM:\\Software\\Policies" -Name "DisableAV" -Value 1', cwd=ws["root"])
        assert eff.protected_hit is True
        assert tier_from_effect(eff) == "privileged"
        assert any("HKLM" in r for r in eff.registry_writes)

    def test_powershell_hkcu_registry_is_state_modifying(self, win_temp_workspace):
        ws = win_temp_workspace
        eff = analyse('Set-ItemProperty -Path "HKCU:\\Software\\MyApp" -Name "Setting" -Value 42', cwd=ws["root"])
        assert eff.protected_hit is False
        assert tier_from_effect(eff) == "state_modifying"
        assert any("HKCU" in r for r in eff.registry_writes)

    def test_cmd_reg_add_hklm_is_privileged(self, win_temp_workspace):
        ws = win_temp_workspace
        eff = analyse('reg add "HKLM\\Software\\Test" /v "Key" /t REG_SZ /d "123" /f', cwd=ws["root"])
        assert eff.protected_hit is True
        assert tier_from_effect(eff) == "privileged"

    def test_cmd_reg_delete_is_destructive(self, win_temp_workspace):
        ws = win_temp_workspace
        eff = analyse('reg delete "HKCU\\Software\\OldApp" /f', cwd=ws["root"])
        assert tier_from_effect(eff) == "destructive"
        assert any("registry:" in d for d in eff.deletes)


class TestHardBlocksAndOpaqueExecution:
    """Test format/diskpart hard blocks, -EncodedCommand, Invoke-Expression, and RunAs."""

    def test_format_is_hard_blocked(self, win_temp_workspace):
        ws = win_temp_workspace
        eff = analyse("format D: /FS:NTFS /Q", cwd=ws["root"])
        assert eff.hard_block is True
        assert eff.protected_hit is True
        assert tier_from_effect(eff) == "privileged"
        msg = preview(eff)
        assert "HARD BLOCKED" in msg

    def test_diskpart_is_hard_blocked(self, win_temp_workspace):
        ws = win_temp_workspace
        eff = analyse("diskpart /s wipe.txt", cwd=ws["root"])
        assert eff.hard_block is True
        assert eff.protected_hit is True
        assert tier_from_effect(eff) == "privileged"
        msg = preview(eff)
        assert "HARD BLOCKED" in msg

    def test_invoke_expression_is_opaque(self, win_temp_workspace):
        ws = win_temp_workspace
        eff = analyse('Invoke-Expression "Remove-Item foo.txt"', cwd=ws["root"])
        assert eff.opaque is True
        assert tier_from_effect(eff) == "ambiguous"

        eff_iex = analyse('iex "del secret.txt"', cwd=ws["root"])
        assert eff_iex.opaque is True
        assert tier_from_effect(eff_iex) == "ambiguous"

    def test_encoded_command_is_opaque(self, win_temp_workspace):
        ws = win_temp_workspace
        eff = analyse("powershell.exe -EncodedCommand JAB4ID0gMQ==", cwd=ws["root"])
        assert eff.opaque is True
        assert tier_from_effect(eff) == "ambiguous"

        eff_pwsh = analyse("pwsh -enc JAB4ID0gMQ==", cwd=ws["root"])
        assert eff_pwsh.opaque is True
        assert tier_from_effect(eff_pwsh) == "ambiguous"

    def test_start_process_runas_is_privileged(self, win_temp_workspace):
        ws = win_temp_workspace
        eff = analyse("Start-Process powershell.exe -Verb RunAs", cwd=ws["root"])
        assert eff.protected_hit is True
        assert tier_from_effect(eff) == "privileged"
        msg = preview(eff)
        assert "PROTECTED" in msg

    def test_runas_cmd_is_privileged(self, win_temp_workspace):
        ws = win_temp_workspace
        eff = analyse("runas /user:Administrator cmd.exe", cwd=ws["root"])
        assert eff.protected_hit is True
        assert tier_from_effect(eff) == "privileged"
