"""
tests/test_virtual_overlay.py - Unit and Integration Tests for Task T36.

Tests:
1. VirtualFS copy-on-write isolation (zero disk mutation).
2. Whiteout and opaque directory semantics (OverlayFS replacement).
3. Glob wildcard resolution and recursive walk over in-memory nodes.
4. Multi-step plan dependency tracking (Step k conditioned on steps 1..k-1).
5. 10-step plan bound enforcement (max 10 steps).
6. Combined plan preview, net risk tier, and Cancel default action for destructive tiers.
7. Atomic execution under single UndoGroup and byte-identical manual rollback.
8. Automatic stop and rollback upon step failure or privilege violation.
9. 50 Generated Multi-Step Plans Benchmark: predicted vs actual manifest diffs (100% concordance).
"""

from __future__ import annotations

import os
import random
import shutil
import tempfile
from pathlib import Path

import pytest

from nlagent.plan.engine import (
    MAX_PLAN_STEPS,
    CombinedPlanPreview,
    MultiStepPlan,
    PlanEngine,
    PlanLimitExceededError,
    StepExecutionRecord,
    UndoGroup,
)
from nlagent.safety.manifest import diff as manifest_diff, manifest as build_manifest
from nlagent.safety.overlay import VirtualFS, analyse_on_overlay


@pytest.fixture
def temp_workspace():
    """Create an isolated temporary workspace directory."""
    temp_dir = tempfile.mkdtemp(prefix="nlagent_vfs_test_")
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def temp_quarantine():
    """Create an isolated temporary quarantine directory."""
    temp_q = tempfile.mkdtemp(prefix="nlagent_plan_q_")
    yield temp_q
    shutil.rmtree(temp_q, ignore_errors=True)


def test_virtual_fs_basic_cow(temp_workspace):
    """VirtualFS mutations must stay in memory and never touch the physical disk."""
    # Create a base file on physical disk
    base_file = Path(temp_workspace) / "existing.txt"
    base_file.write_text("hello world", encoding="utf-8")

    vfs = VirtualFS(base_cwd=temp_workspace)
    assert vfs.exists("existing.txt")
    assert vfs.isfile("existing.txt")
    assert vfs.getsize("existing.txt") == 11

    # Perform virtual writes and directory creations
    vfs.mkdir("sub/nested")
    vfs.write_file("sub/nested/virtual.log", content="virtual data")
    vfs.append_file("existing.txt", added_content=" more data")

    # Assert visible in overlay
    assert vfs.isdir("sub/nested")
    assert vfs.exists("sub/nested/virtual.log")
    assert vfs.exists("existing.txt")
    assert vfs.getsize("existing.txt") > 11

    # Assert physical disk is completely untouched
    assert not (Path(temp_workspace) / "sub").exists()
    assert base_file.read_text(encoding="utf-8") == "hello world"


def test_virtual_fs_whiteouts_and_opaque_directories(temp_workspace):
    """Deleting a directory and recreating it must hide underlying base-disk items (OverlayFS opaque)."""
    # Create base structure: build/old1.txt, build/old2.txt
    build_dir = Path(temp_workspace) / "build"
    build_dir.mkdir()
    (build_dir / "old1.txt").write_text("old1", encoding="utf-8")
    (build_dir / "old2.txt").write_text("old2", encoding="utf-8")

    vfs = VirtualFS(base_cwd=temp_workspace)
    assert len(vfs.listdir("build")) == 2

    # Delete build recursively in overlay
    vfs.delete("build", recursive=True)
    assert not vfs.exists("build")
    assert not vfs.exists("build/old1.txt")
    assert vfs.listdir("build") == []

    # Recreate build in overlay: must become an opaque directory hiding old1 and old2
    vfs.mkdir("build")
    assert vfs.isdir("build")
    assert not vfs.exists("build/old1.txt")
    assert not vfs.exists("build/old2.txt")
    assert vfs.listdir("build") == []

    # Create new file in build
    vfs.write_file("build/new.txt", content="new")
    assert vfs.exists("build/new.txt")
    assert vfs.listdir("build") == ["new.txt"]


def test_virtual_fs_glob_and_walk(temp_workspace):
    """Wildcards and directory walks must resolve cleanly across hybrid base+overlay state."""
    (Path(temp_workspace) / "file1.txt").write_text("1", encoding="utf-8")
    (Path(temp_workspace) / "file2.log").write_text("2", encoding="utf-8")

    vfs = VirtualFS(base_cwd=temp_workspace)
    vfs.write_file("file3.txt", content="3")
    vfs.mkdir("logs")
    vfs.write_file("logs/app1.log", content="log1")
    vfs.write_file("logs/app2.log", content="log2")

    # Glob tests
    txt_matches = [os.path.basename(p) for p in vfs.glob("*.txt")]
    assert sorted(txt_matches) == ["file1.txt", "file3.txt"]

    log_matches = [os.path.basename(p) for p in vfs.glob("logs/*.log")]
    assert sorted(log_matches) == ["app1.log", "app2.log"]

    # Walk test
    all_walked = list(vfs.walk(temp_workspace))
    top_dir, dirnames, filenames = all_walked[0]
    assert "logs" in dirnames
    assert "file1.txt" in filenames
    assert "file2.log" in filenames
    assert "file3.txt" in filenames


def test_multi_step_dependency_simulation(temp_workspace):
    """Step k's effect must be accurately conditioned on mutations made in steps 1..k-1."""
    (Path(temp_workspace) / "initial.txt").write_text("initial payload", encoding="utf-8")

    vfs = VirtualFS(base_cwd=temp_workspace)

    # Step 1: Create a staging directory
    eff1 = analyse_on_overlay("mkdir staging", vfs=vfs, mutate=True)
    assert any("staging" in p for p in eff1.creates)

    # Step 2: Copy initial.txt into staging/copied.txt
    eff2 = analyse_on_overlay("cp initial.txt staging/copied.txt", vfs=vfs, mutate=True)
    assert any("copied.txt" in p for p in eff2.creates)

    # Step 3: Delete initial.txt
    eff3 = analyse_on_overlay("rm initial.txt", vfs=vfs, mutate=True)
    assert any("initial.txt" in p for p in eff3.deletes)

    # Step 4: Overwrite staging/copied.txt
    eff4 = analyse_on_overlay("echo 'updated' > staging/copied.txt", vfs=vfs, mutate=True)
    # staging/copied.txt was created in Step 2, so in Step 4 it exists in overlay -> overwrites!
    assert any("copied.txt" in p for p in eff4.overwrites)

    # Assert zero changes on physical disk
    assert (Path(temp_workspace) / "initial.txt").exists()
    assert not (Path(temp_workspace) / "staging").exists()


def test_max_10_steps_enforcement(temp_workspace):
    """Plans must enforce the 10-step boundary and reject 0 or 11 steps."""
    engine = PlanEngine(max_steps=MAX_PLAN_STEPS)

    with pytest.raises(PlanLimitExceededError):
        engine.create_plan([], cwd=temp_workspace)

    eleven_steps = [f"touch file_{i}.txt" for i in range(1, 12)]
    with pytest.raises(PlanLimitExceededError):
        engine.create_plan(eleven_steps, cwd=temp_workspace)

    ten_steps = [f"touch file_{i}.txt" for i in range(1, 11)]
    plan = engine.create_plan(ten_steps, cwd=temp_workspace)
    assert len(plan.steps) == 10


def test_combined_preview_and_cancel_default(temp_workspace):
    """Destructive multi-step plans must mandate Cancel default and summarize net effects."""
    (Path(temp_workspace) / "delete_me.txt").write_text("important data", encoding="utf-8")

    engine = PlanEngine()
    plan = engine.create_plan(
        [
            "mkdir output",
            "touch output/report.csv",
            "rm delete_me.txt",
        ],
        cwd=temp_workspace,
    )

    preview = plan.preview
    assert preview.combined_tier == "destructive"
    assert preview.default_action == "cancel"
    assert "delete_me.txt" in preview.net_manifest_diff["deleted"]
    assert "output" in preview.net_manifest_diff["created"]
    assert "output/report.csv" in preview.net_manifest_diff["created"] or "output\\report.csv" in preview.net_manifest_diff["created"]
    assert "delete" in preview.plain_english_headline.lower()


def test_plan_execution_and_atomic_undo_group(temp_workspace, temp_quarantine):
    """Multi-step plan execution must capture an UndoGroup and allow byte-identical rollback."""
    (Path(temp_workspace) / "original.txt").write_text("pre-existing content", encoding="utf-8")
    before_manifest = build_manifest(temp_workspace)

    engine = PlanEngine(quarantine_base_dir=temp_quarantine)
    plan = engine.create_plan(
        [
            "mkdir assets",
            "echo 'asset data' > assets/icon.svg",
            "rm original.txt",
        ],
        cwd=temp_workspace,
    )

    # Execute plan on real disk
    result = engine.execute_plan(plan)
    assert result.success is True
    assert result.completed_steps == 3
    assert not (Path(temp_workspace) / "original.txt").exists()
    assert (Path(temp_workspace) / "assets" / "icon.svg").exists()

    # Roll back entire group atomically
    undo_result = engine.rollback_group(result.group_id)
    assert undo_result.success is True
    assert (Path(temp_workspace) / "original.txt").exists()
    assert (Path(temp_workspace) / "original.txt").read_text(encoding="utf-8") == "pre-existing content"
    assert not (Path(temp_workspace) / "assets").exists()

    after_rollback_manifest = build_manifest(temp_workspace)
    assert manifest_diff(before_manifest, after_rollback_manifest) == {
        "deleted": [],
        "created": [],
        "modified": [],
        "perm": [],
    }


def test_plan_automatic_stop_and_rollback_on_failure(temp_workspace, temp_quarantine):
    """If step k fails, execution must immediately stop and automatically roll back steps 1..k-1."""
    (Path(temp_workspace) / "safe_file.txt").write_text("initial safe", encoding="utf-8")
    before_manifest = build_manifest(temp_workspace)

    engine = PlanEngine(quarantine_base_dir=temp_quarantine)

    # Step 1: creates dir, Step 2: deletes safe_file.txt, Step 3: invalid command that fails, Step 4: should never run
    plan = engine.create_plan(
        [
            "mkdir test_dir",
            "rm safe_file.txt",
            "nonexistent_binary_will_fail_12345",
            "touch should_never_exist.txt",
        ],
        cwd=temp_workspace,
    )

    from nlagent.plan.engine import default_plan_runner

    # Custom runner that fails on step 3
    def failing_runner(cmd, cwd, env):
        if "nonexistent_binary" in cmd:
            return "Command failed: exit 127", 127, cwd, env
        return default_plan_runner(cmd, cwd, env)

    res = engine.execute_plan(plan, runner=failing_runner)

    assert res.success is False
    assert res.failed_step_index == 3
    assert res.completed_steps == 2
    assert res.rolled_back is True
    assert not (Path(temp_workspace) / "should_never_exist.txt").exists()
    assert not (Path(temp_workspace) / "test_dir").exists()
    assert (Path(temp_workspace) / "safe_file.txt").exists()
    assert (Path(temp_workspace) / "safe_file.txt").read_text(encoding="utf-8") == "initial safe"

    after_failure_manifest = build_manifest(temp_workspace)
    assert manifest_diff(before_manifest, after_failure_manifest) == {
        "deleted": [],
        "created": [],
        "modified": [],
        "perm": [],
    }


def test_predicted_vs_actual_manifest_diffs_50_plans(temp_workspace, temp_quarantine):
    """
    Task T36 Benchmark: 50 randomly generated multi-step plans in temp directories.
    Asserts 100% concordance between predicted manifest diff (from overlay)
    and actual manifest diff (from physical disk execution), plus clean rollback.
    """
    rng = random.Random(42)
    engine = PlanEngine(quarantine_base_dir=temp_quarantine)

    concordance_count = 0

    for plan_idx in range(1, 51):
        plan_workspace = os.path.join(temp_workspace, f"plan_test_{plan_idx:02d}")
        os.makedirs(plan_workspace, exist_ok=True)

        # 1. Seed workspace with base files and a subdirectory
        (Path(plan_workspace) / "base_a.txt").write_text("data a", encoding="utf-8")
        (Path(plan_workspace) / "base_b.txt").write_text("data b", encoding="utf-8")
        sub_dir = Path(plan_workspace) / "initial_sub"
        sub_dir.mkdir(exist_ok=True)
        (sub_dir / "base_c.csv").write_text("1,2,3", encoding="utf-8")

        initial_manifest = build_manifest(plan_workspace)

        # 2. Causally consistent plan generator tracking live files and directories
        live_files: list[str] = ["base_a.txt", "base_b.txt", "initial_sub/base_c.csv"]
        live_dirs: list[str] = ["initial_sub"]

        num_steps = rng.randint(3, 7)
        commands: list[str] = []

        for step_i in range(1, num_steps + 1):
            action_pool = ["mkdir", "create_file"]
            if live_files:
                action_pool.extend(["copy_file", "append_file", "move_file"])
            if len(live_files) > 1:
                action_pool.append("delete_file")

            chosen_action = rng.choice(action_pool)

            if chosen_action == "mkdir":
                dname = f"dir_{plan_idx}_{step_i}"
                commands.append(f"mkdir {dname}")
                live_dirs.append(dname)

            elif chosen_action == "create_file":
                parent_dir = rng.choice([""] + live_dirs)
                fname = f"f_{plan_idx}_{step_i}.txt"
                rel_path = f"{parent_dir}/{fname}" if parent_dir else fname
                commands.append(f"echo 'content_{plan_idx}_{step_i}' > {rel_path}")
                live_files.append(rel_path)

            elif chosen_action == "copy_file":
                src = rng.choice(live_files)
                parent_dir = rng.choice([""] + live_dirs)
                dst = f"{parent_dir}/cp_{plan_idx}_{step_i}.txt" if parent_dir else f"cp_{plan_idx}_{step_i}.txt"
                commands.append(f"cp {src} {dst}")
                live_files.append(dst)

            elif chosen_action == "append_file":
                target = rng.choice(live_files)
                commands.append(f"echo 'extra_{step_i}' >> {target}")

            elif chosen_action == "move_file":
                src = rng.choice(live_files)
                live_files.remove(src)
                parent_dir = rng.choice([""] + live_dirs)
                dst = f"{parent_dir}/mv_{plan_idx}_{step_i}.txt" if parent_dir else f"mv_{plan_idx}_{step_i}.txt"
                commands.append(f"mv {src} {dst}")
                live_files.append(dst)

            elif chosen_action == "delete_file":
                target = rng.choice(live_files)
                live_files.remove(target)
                commands.append(f"rm {target}")

        # 3. Compile plan and compute predicted manifest diff on overlay
        plan = engine.create_plan(commands, cwd=plan_workspace)
        predicted_diff = plan.preview.net_manifest_diff

        # 4. Execute on real disk
        exec_res = engine.execute_plan(plan)
        assert exec_res.success is True, f"Plan {plan_idx} failed execution: {exec_res.error}"

        # 5. Measure actual manifest diff on real disk
        post_exec_manifest = build_manifest(plan_workspace)
        actual_diff = manifest_diff(initial_manifest, post_exec_manifest)

        # 6. Verify exact concordance between predicted and actual
        norm_pred = {k: sorted(v) for k, v in predicted_diff.items()}
        norm_act = {k: sorted(v) for k, v in actual_diff.items()}

        assert norm_pred == norm_act, (
            f"Discrepancy in Plan {plan_idx}!\n"
            f"Predicted: {norm_pred}\n"
            f"Actual:    {norm_act}\n"
            f"Commands:  {commands}"
        )
        concordance_count += 1

        # 7. Verify atomic rollback restores original disk state exactly
        rb_res = engine.rollback_group(exec_res.group_id)
        assert rb_res.success is True, f"Plan {plan_idx} rollback failed: {rb_res.message}"

        restored_manifest = build_manifest(plan_workspace)
        rb_diff = manifest_diff(initial_manifest, restored_manifest)
        assert rb_diff == {"deleted": [], "created": [], "modified": [], "perm": []}, (
            f"Plan {plan_idx} rollback residual diff: {rb_diff}"
        )

        shutil.rmtree(plan_workspace, ignore_errors=True)

    assert concordance_count == 50


def test_plan_cd_and_env_var_propagation(temp_workspace, temp_quarantine):
    """Steps mutating env vars and working directory via cd must propagate cleanly across overlay and execution."""
    engine = PlanEngine(quarantine_base_dir=temp_quarantine)
    before_manifest = build_manifest(temp_workspace)

    plan = engine.create_plan(
        [
            "export OUTDIR=stage_out",
            "mkdir $OUTDIR",
            "cd $OUTDIR",
            "echo 'hello stage' > output.txt",
            "cd ..",
        ],
        cwd=temp_workspace,
    )

    predicted_created = sorted(plan.preview.net_manifest_diff["created"])
    expected_nested = os.path.join("stage_out", "output.txt")
    assert sorted(["stage_out", expected_nested]) == predicted_created

    exec_res = engine.execute_plan(plan)
    assert exec_res.success is True
    actual_diff = manifest_diff(before_manifest, build_manifest(temp_workspace))
    assert sorted(actual_diff["created"]) == predicted_created

    # Rollback cleanly
    rb_res = engine.rollback_group(exec_res.group_id)
    assert rb_res.success is True
    assert manifest_diff(before_manifest, build_manifest(temp_workspace)) == {
        "deleted": [],
        "created": [],
        "modified": [],
        "perm": [],
    }


def test_plan_delete_and_recreate_directory_opaque_overlay(temp_workspace, temp_quarantine):
    """Deleting a populated dir then recreating it and adding a new file must yield exact manifest diffs & undo."""
    build_dir = Path(temp_workspace) / "dist"
    build_dir.mkdir()
    (build_dir / "old_a.bin").write_text("old_a", encoding="utf-8")
    (build_dir / "old_b.bin").write_text("old_b", encoding="utf-8")

    before_manifest = build_manifest(temp_workspace)
    engine = PlanEngine(quarantine_base_dir=temp_quarantine)

    plan = engine.create_plan(
        [
            "rm -r dist",
            "mkdir dist",
            "echo 'fresh' > dist/new_c.bin",
        ],
        cwd=temp_workspace,
    )

    pred_diff = plan.preview.net_manifest_diff
    assert sorted(pred_diff["deleted"]) == sorted([os.path.join("dist", "old_a.bin"), os.path.join("dist", "old_b.bin")])
    assert sorted(pred_diff["created"]) == [os.path.join("dist", "new_c.bin")]

    exec_res = engine.execute_plan(plan)
    assert exec_res.success is True
    actual_diff = manifest_diff(before_manifest, build_manifest(temp_workspace))
    assert {k: sorted(v) for k, v in actual_diff.items()} == {k: sorted(v) for k, v in pred_diff.items()}

    # Verify rollback restores BOTH deleted files and removes new_c.bin
    rb_res = engine.rollback_group(exec_res.group_id)
    assert rb_res.success is True
    assert manifest_diff(before_manifest, build_manifest(temp_workspace)) == {
        "deleted": [],
        "created": [],
        "modified": [],
        "perm": [],
    }


def test_plan_repeated_mutation_and_combined_banner(temp_workspace, temp_quarantine):
    """Test multiple consecutive mutations to same file and verify combined preview banner formatting."""
    target = Path(temp_workspace) / "doc.txt"
    target.write_text("v1 content", encoding="utf-8")
    before_manifest = build_manifest(temp_workspace)

    engine = PlanEngine(quarantine_base_dir=temp_quarantine)
    plan = engine.create_plan(
        [
            "echo 'v2 append' >> doc.txt",
            "mv doc.txt archived_doc.txt",
            "echo 'brand new doc' > doc.txt",
        ],
        cwd=temp_workspace,
    )

    banner = plan.preview.render_banner()
    assert "Multi-Step Plan (3 steps)" in banner
    assert "Combined Risk : [DESTRUCTIVE]" in banner
    assert "Default Action: [CANCEL]" in banner

    pred_diff = plan.preview.net_manifest_diff
    assert pred_diff["modified"] == ["doc.txt"]
    assert pred_diff["created"] == ["archived_doc.txt"]
    assert pred_diff["deleted"] == []

    exec_res = engine.execute_plan(plan)
    assert exec_res.success is True
    actual_diff = manifest_diff(before_manifest, build_manifest(temp_workspace))
    assert {k: sorted(v) for k, v in actual_diff.items()} == {k: sorted(v) for k, v in pred_diff.items()}

    # Atomic rollback restores v1 doc.txt cleanly and deletes archived_doc.txt
    rb_res = engine.rollback_group(exec_res.group_id)
    assert rb_res.success is True
    assert target.read_text(encoding="utf-8") == "v1 content"
    assert not (Path(temp_workspace) / "archived_doc.txt").exists()
