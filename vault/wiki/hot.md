# Vault Hot Context (`wiki/hot.md`)

*Last updated: 2026-11-10T10:00:00Z*  
*Active Milestone: Phase E — Productization & Multi-Step Plans*

---

## 1. Current State & Immediate Task
- **Completed**:
  - Phase A & B: Evidence repair, statistical intervals (`stats.py`), evidence ledger audit (`scripts/build_evidence_ledger.py`), cross-shell AST analysis (PowerShell & cmd.exe), safety benchmark v3, ablation harness.
  - Phase C: Standalone Runtime Manager (`src/nlagent/runtime/manager.py`), system doctor (`nlagent doctor`), and Model Store with resumable HTTP range downloads (`src/nlagent/runtime/models.py`).
  - Task N1: Dev Vault, Agent Memory & Vault Linter (`scripts/vault_lint.py`).
  - Task T31: Core Service Daemon on loopback with ephemeral bearer token API (`src/nlagent/service/core.py`, `src/nlagent/service/api.py`), `nlctl service {start,stop,status}`, and seamless in-process fallback.
  - Task N2a: Bounded Read-Only Context Grounding, UserProfile Approve-Before-Save Governance, and Hostile Filename Injection Sanitizer (`src/nlagent/context/grounding.py`, `profile.py`, `sanitize.py`).
  - Task T32: Deterministic Plain-English Consequence Renderer (`src/nlagent/safety/renderer.py`) with Cancel default for destructive tiers.
  - Task N2b: Grounding Integration into `propose()` with byte-identical L0/L1 prefixes, Context Evaluation Benchmark (`scripts/run_context_eval.py`), and Injection Immunity Suite (`tests/test_injection.py`).
  - Task T25/T40: Live E5 Baselines on Dev Split (`scripts/run_e5.py`, [`results/e5_dev_summary.md`](../../results/e5_dev_summary.md)). Strict dev partition (N=100), mock backend exclusion, exact McNemar tests, paired accuracy intervals.
  - Task N4: Autoresearch Optimization Loops & Audited Sealed Gate (`scripts/run_loop.py`, `experiments/loops/prompt_opt/program.md`, `scripts/run_sealed.py`, [`results.tsv`](../../experiments/loops/prompt_opt/results.tsv)). Single-file confinement, 95% paired bootstrap interval decision rules, append-only ledger.
  - Task T42: Adaptive Adversarial Attacker & Multi-Stage Evasions (`scripts/run_adaptive_attacker.py`, `results/adaptive_attacks.jsonl`, [`results/adaptive_attacks_summary.md`](../../results/adaptive_attacks_summary.md)). 816 multi-stage trials across PowerShell, cmd.exe, and Bash; 98.5% interception on Combined G0-G3 vs 71.5% bypass on G0-simple with exact Clopper-Pearson intervals.
  - Task T37/N6: Hardware Matrix Benchmark & Install Autotuner (`src/nlagent/runtime/autotune.py`, `scripts/run_hardware_matrix.py`, [`results/hardware_matrix_summary.md`](../../results/hardware_matrix_summary.md)). 90s time-capped autotuner with safe CPU fallback, cross-tier evaluation across CPU, Vulkan, and CUDA tiers (24.5 ms TTFT on CUDA, 4.9x speedup).
  - Step 10 (SEALED Freeze): Frozen Final Benchmark Runs (`scripts/run_sealed.py --freeze`, [`results/sealed_final.jsonl`](../../results/sealed_final.jsonl), [`results/sealed_final_summary.md`](../../results/sealed_final_summary.md)). Evaluated frozen Git SHA `dfce40e5` across sealed partitions (`e5_sealed.jsonl` N=50 and `safety_v3_sealed.jsonl` N=100); 300 authenticated rows with full 10-field provenance schema, 100% VALID in [`docs/evidence_ledger.md`](../../docs/evidence_ledger.md). E5 synthesis: 94.0% Tuned vs 32.0% Base ($p < 10^{-6}$); Safety guard: 98.3% interception on Combined G0-G3.
  - Task N3: Monotone Learned Escalation (`scripts/run_learned_escalation.py`, [`results/learned_escalation.jsonl`](../../results/learned_escalation.jsonl), [`results/learned_escalation_summary.md`](../../results/learned_escalation_summary.md)). Tabular risk models (LR, GBDT) on 27 AST effect features. Proved Monotone Risk Escalation Principle ($T_{comb} = \max(T_{det}, T_{learned})$). 16/16 trials valid in ledger.
  - Task T45: Automated Paper Numbers Gate (`scripts/gate_paper_numbers.py`, `paper/main.tex`, `tests/test_paper_gate.py`). Zero-tolerance concordance checker verifying paper quantitative claims against ledger. 121/121 claims verified, 0 violations.
  - Tasks T33-T35 + N7: Desktop UI, Setup Wizard, Inno Installer & Usability Study Mode (`src/nlagent/ui/`, `installer/setup.iss`, `tests/test_study_mode.py`). PySide6 UI with floating preview window, Cancel-default on destructive tiers, air-gapped local JSONL study logger (`results/study_trials.jsonl`), condition allocator, and first-run setup wizard.
  - Task T36: Multi-Step Plans on Virtual FS Overlay (`src/nlagent/safety/overlay.py`, `src/nlagent/plan/engine.py`, `tests/test_virtual_overlay.py`). In-memory copy-on-write virtual filesystem overlay supporting up to 10-step plans. Verified 100% concordance between predicted manifest diffs and actual physical disk diffs across 50 randomly generated plans, combined consequence preview, and atomic rollback via single UndoGroup.
- Active Test Suite: [398 tests passing](../../tests) (100% green).
- **Active Task**: **Milestone Complete** — All Master Roadmap Tasks (T1–T45 & N1–N7) Complete & Verified.
- **Next Task**: Final Project Report Delivery & Slide Defense Presentation.

---

## 2. Active Environment & Hardware Configuration
- **Operating System**: Windows 10 (Build 10.0.26300)
- **Host GPU**: NVIDIA GeForce RTX 2050 Laptop GPU (4 GB VRAM, CUDA 572.42 driver)
- **Host CPU**: AMD64 with 12 logical cores, 15.7 GB total RAM (2.6+ GB free)
- **Active Model Target**: `models/nemotron-3-nano-4b.Q4_K_M.gguf` (SHA-256 registered in [`models/manifest.json`](../../models/manifest.json))
- **Inference Server**: Bundled `llama-server` supervised as hidden child process on random localhost port.

---

## 3. Open Risks & Watch Items
1. **Windows Sandbox Sockets**: Outbound TCP socket `connect()` to 127.0.0.1 raises `WinError 10013` inside standard sandbox runs. All test fixtures must support in-process transport fallbacks or mock opener routing to guarantee 100% sandboxed test passage.
2. **Path Hallucination**: Current LLM translation relies solely on compact state without disk grounding. Must implement **N2a/N2b** bounded search to inject verified real paths before running the live E5 benchmark.
3. **Data Leakage into Sealed Sets**: Dev loops (**N4**) must operate strictly on `datasets/dev_v3.jsonl`. Sealed evaluation files (`datasets/safety_v3_sealed.jsonl`) must remain untouched until configuration freeze.

---

## 4. Key Links
- Evidence Ledger: [`docs/evidence_ledger.md`](../../docs/evidence_ledger.md)
- Master Execution Roadmap: [`Ref_docs/EXECUTION_ROADMAP_AND_PROMPTS.md`](../../Ref_docs/EXECUTION_ROADMAP_AND_PROMPTS.md)
- Knowledge Index: [`wiki/index.md`](index.md)
- Changelog: [`wiki/log.md`](log.md)

