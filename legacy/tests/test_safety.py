# tests/test_safety.py
"""
Unit tests for Safety Verifier and Risk Classification.
"""

import sys
import os

# Add parent directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from safety_verifier import classify_risk, RiskTier, sandbox_simulate


class TestRiskClassification:
    def test_ls_is_readonly(self):
        assert classify_risk("ls -la") == RiskTier.READ_ONLY

    def test_cat_is_readonly(self):
        assert classify_risk("cat file.txt") == RiskTier.READ_ONLY

    def test_ps_is_readonly(self):
        assert classify_risk("ps aux") == RiskTier.READ_ONLY

    def test_df_is_readonly(self):
        assert classify_risk("df -h") == RiskTier.READ_ONLY

    def test_mkdir_is_modifying(self):
        assert classify_risk("mkdir backup") == RiskTier.STATE_MODIFYING

    def test_cp_is_modifying(self):
        assert classify_risk("cp file.txt backup/") == RiskTier.STATE_MODIFYING

    def test_touch_is_modifying(self):
        assert classify_risk("touch newfile.txt") == RiskTier.STATE_MODIFYING

    def test_rm_rf_is_destructive(self):
        assert classify_risk("rm -rf /tmp/test") == RiskTier.DESTRUCTIVE

    def test_rmdir_is_destructive(self):
        assert classify_risk("rmdir old_dir") == RiskTier.DESTRUCTIVE

    def test_kill_is_destructive(self):
        assert classify_risk("kill -9 1234") == RiskTier.DESTRUCTIVE

    def test_piped_rm_is_destructive(self):
        # Security bypass attempt: piped destructive command
        assert classify_risk("echo 'test' | rm -rf /tmp") == RiskTier.DESTRUCTIVE

    def test_piped_bash_eval_is_destructive(self):
        # Security bypass attempt: piping destructive command to bash
        assert classify_risk("echo 'rm -rf /' | bash") == RiskTier.DESTRUCTIVE

    def test_find_delete_is_destructive(self):
        # Security bypass attempt: find -delete instead of rm
        assert classify_risk("find /tmp -delete") == RiskTier.DESTRUCTIVE

    def test_redirect_escalates_to_modifying(self):
        # Read-only command with redirection writes files
        assert classify_risk("cat file.txt > output.txt") == RiskTier.STATE_MODIFYING

    def test_empty_is_ambiguous(self):
        assert classify_risk("") == RiskTier.AMBIGUOUS
        assert classify_risk("   ") == RiskTier.AMBIGUOUS


class TestSandboxSimulation:
    def test_simulation_fallback_or_docker(self):
        result = sandbox_simulate("rm -rf /tmp/test", cwd=".")
        assert "summary" in result
        assert isinstance(result["summary"], str)
        assert len(result["summary"]) > 0
