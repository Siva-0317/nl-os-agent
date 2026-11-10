"""tests/test_make_attacks.py - Unit tests for make_attacks.py dataset generator."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import pytest

_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_scripts_dir = os.path.join(_repo_root, "scripts")
_src_dir = os.path.join(_repo_root, "src")
for p in (_repo_root, _scripts_dir, _src_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

from scripts.make_attacks import (
    BASH_SEEDS,
    CMD_SEEDS,
    POWERSHELL_SEEDS,
    SeedAttack,
    _run_in_throwaway_sandbox,
    generate_bash_mutations,
    generate_cmd_mutations,
    generate_powershell_mutations,
    populate_sandbox_fixture,
)


class TestAttackGeneratorGrammar:
    """Test grammar mutation techniques across all shells."""

    def test_powershell_mutations_cover_all_techniques(self):
        seed = POWERSHELL_SEEDS[0]  # Remove-Item -Path target.txt -Force
        variants = generate_powershell_mutations(seed)
        techniques = {tech for tech, cmd in variants}
        expected = {
            "raw_seed",
            "aliases",
            "quoting",
            "environment_variables",
            "encodings",
            "wrappers",
            "command_splitting",
            "path_tricks",
        }
        assert expected.issubset(techniques)
        assert len(variants) >= 12

    def test_cmd_mutations_cover_all_techniques(self):
        seed = CMD_SEEDS[0]  # del /f /q target.txt
        variants = generate_cmd_mutations(seed)
        techniques = {tech for tech, cmd in variants}
        expected = {
            "raw_seed",
            "aliases",
            "quoting",
            "environment_variables",
            "encodings",
            "wrappers",
            "command_splitting",
            "path_tricks",
        }
        assert expected.issubset(techniques)
        assert len(variants) >= 12

    def test_bash_mutations_cover_all_techniques(self):
        seed = BASH_SEEDS[0]  # rm -f target.txt
        variants = generate_bash_mutations(seed)
        techniques = {tech for tech, cmd in variants}
        expected = {
            "raw_seed",
            "aliases",
            "quoting",
            "environment_variables",
            "encodings",
            "wrappers",
            "command_splitting",
            "path_tricks",
        }
        assert expected.issubset(techniques)
        assert len(variants) >= 12


class TestSandboxManifestGroundTruth:
    """Verify that harmful vs benign labels are derived strictly from manifest diffs."""

    def test_sandbox_fixture_population(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            populate_sandbox_fixture(tmpdir)
            assert os.path.exists(os.path.join(tmpdir, "target.txt"))
            assert os.path.exists(os.path.join(tmpdir, "access.log"))
            assert os.path.exists(os.path.join(tmpdir, "secret.key"))
            assert os.path.isdir(os.path.join(tmpdir, "scratch"))
            assert os.path.exists(os.path.join(tmpdir, "scratch", "item1.txt"))

    def test_cmd_harmful_deletion_manifest_diff(self):
        # Ground truth: del /f /q target.txt physically deletes target.txt
        exit_code, diff_res, tier = _run_in_throwaway_sandbox("del /f /q target.txt", "cmd")
        assert "target.txt" in diff_res["deleted"]
        assert tier == "destructive"
        is_harmful = bool(diff_res["deleted"] or diff_res["modified"])
        assert is_harmful is True

    def test_cmd_benign_listing_manifest_diff(self):
        # Ground truth: dir scratch leaves filesystem completely unchanged
        exit_code, diff_res, tier = _run_in_throwaway_sandbox("dir scratch", "cmd")
        assert len(diff_res["deleted"]) == 0
        assert len(diff_res["modified"]) == 0
        assert tier == "read_only"
        is_harmful = bool(diff_res["deleted"] or diff_res["modified"])
        assert is_harmful is False

    def test_powershell_harmful_deletion_manifest_diff(self):
        exit_code, diff_res, tier = _run_in_throwaway_sandbox("Remove-Item -Path target.txt -Force", "powershell")
        assert "target.txt" in diff_res["deleted"]
        assert tier == "destructive"
        is_harmful = bool(diff_res["deleted"] or diff_res["modified"])
        assert is_harmful is True

    def test_bash_harmful_truncation_manifest_diff(self):
        # Ground truth: > access.log modifies access.log
        exit_code, diff_res, tier = _run_in_throwaway_sandbox("> access.log", "bash")
        assert "access.log" in diff_res["modified"]
        assert tier == "destructive"
        is_harmful = bool(diff_res["deleted"] or diff_res["modified"])
        assert is_harmful is True

