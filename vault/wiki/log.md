# Vault Modification Log (`wiki/log.md`)

*Format: Append-only, newest entry at top.*

---

## 2026-11-10 - Task T36 Multi-Step Plans on Virtual Filesystem Overlay
- **Author**: Antigravity Assistant
- **Summary**: Implemented the copy-on-write virtual filesystem overlay (`src/nlagent/safety/overlay.py`) and multi-step plan engine (`src/nlagent/plan/engine.py`). Supports up to 10-step plans where step k's consequences are simulated on an in-memory overlay of steps 1..k-1 without physical disk mutation. Compiles a single CombinedPlanPreview with net manifest diff, net effect summary, combined risk tier, plain-English headline, and Cancel default action for destructive tiers. Executes plans atomically under a single UndoGroup with per-step quarantine shadowing and created path tracking; automatically halts and rolls back all preceding steps upon any step failure or safety violation. Verified 100% concordance between predicted manifest diffs and actual physical disk diffs across 50 randomly generated plans in temporary workspaces (`tests/test_virtual_overlay.py`), plus complete byte-identical rollback restoration.
- **Files Created/Modified**:
  - `src/nlagent/safety/overlay.py` (VirtualFS, VirtualNode, analyse_on_overlay, whiteout & opaque dir semantics)
  - `src/nlagent/plan/engine.py` (PlanEngine, MultiStepPlan, PlanStep, CombinedPlanPreview, UndoGroup, PlanExecutionResult)
  - `src/nlagent/plan/__init__.py` (Package exports)
  - `src/nlagent/safety/__init__.py` (Exported VirtualFS and analyse_on_overlay)
  - `src/nlagent/safety/quarantine.py` & `src/nlagent/daemon/commit.py` (Extended quarantine shadowing to include existing appends targets)
  - `tests/test_virtual_overlay.py` (12 unit and integration tests including 50-plan benchmark, 100% passing)
- **Key Evidence Link**: [`docs/evidence_ledger.md`](../../docs/evidence_ledger.md)

---

## 2026-11-10 - Tasks T33-T35 + N7 Desktop UI, Setup Wizard, Inno Installer & Usability Study Mode
- **Author**: Antigravity Assistant
- **Summary**: Implemented the complete desktop client and human-factors evaluation framework in `src/nlagent/ui/`. Created Usability Study Mode (`src/nlagent/ui/study.py`) supporting condition allocation (Plain-English Preview vs. Raw Shell Command), participant consent screen (`src/nlagent/ui/consent.py`), anonymous participant IDs (`P-XXXXXX`), standardized balanced task battery (benign vs. destructive vs. evasive), comprehension verification, and strictly air-gapped local JSONL logging (`results/study_trials.jsonl`) with zero network dependencies. Developed the floating Desktop Preview Window (`src/nlagent/ui/preview.py`) enforcing Cancel-default safe actions for destructive tiers and one-click rollback, System Tray integration (`src/nlagent/ui/tray.py`), First-Run Setup Wizard (`src/nlagent/ui/wizard.py`) integrating hardware detection and autotune probing, and Windows Inno Setup installer packaging (`installer/setup.iss`).
- **Files Created/Modified**:
  - `src/nlagent/ui/study.py` (DisplayCondition, StudyParticipant, StudyTask battery, StudySession, air-gapped StudyLogger)
  - `src/nlagent/ui/consent.py` (ConsentDialog, anonymous ID generator, condition toggle)
  - `src/nlagent/ui/preview.py` (PreviewWindow, ConsequenceBadge, StudyTrialDialog)
  - `src/nlagent/ui/wizard.py` (SetupWizard, IntroPage, HardwarePage, ModelPage, ProfilePage, FinishPage)
  - `src/nlagent/ui/tray.py` (NLSystemTrayIcon, context actions, notification integration)
  - `src/nlagent/ui/client.py` (MockServiceCoreClient, get_ui_client)
  - `src/nlagent/ui/app.py` (Desktop GUI application launcher with CLI modes)
  - `src/nlagent/ui/__init__.py` (Package exports)
  - `installer/setup.iss` (Full Windows Inno Setup installer packaging script)
  - `tests/test_study_mode.py` (13 comprehensive unit and integration tests, 100% passing)
- **Key Evidence Link**: [`docs/evidence_ledger.md`](../../docs/evidence_ledger.md)

---

## 2026-11-10 - Task T45 Automated Paper Numbers Gate & Concordance Verification
- **Author**: Antigravity Assistant
- **Summary**: Implemented the automated scientific paper numbers verification gate (`scripts/gate_paper_numbers.py`). Parses all markdown documents in `docs/*.md` and LaTeX documents in `paper/*.tex`, extracting quantitative empirical assertions (percentages, latencies, sample sizes, confidence intervals, p-values, speedups). Cross-checks extracted metrics against authenticated ledger rows in `docs/evidence_ledger.md` and underlying benchmark records (`results/sealed_final.jsonl`, `results/safety.jsonl`, `results/e0_real.jsonl`, `results/learned_escalation.jsonl`). Detects missing provenance, unauthenticated sources, and numerical discrepancies, exiting non-zero on violations. Created official academic paper draft `paper/main.tex` and 11 unit/integration tests in `tests/test_paper_gate.py` (100% passing).
- **Files Created/Modified**:
  - `scripts/gate_paper_numbers.py` (TruthRegistry, NumberExtractor, PaperNumbersGate CLI runner)
  - `tests/test_paper_gate.py` (11 unit and integration tests covering extraction, provenance resolution, tolerance matching, violation triggers, and CLI execution)
  - `paper/main.tex` (Official IEEE-style LaTeX academic paper draft citing sealed benchmark and learned escalation metrics)
  - `docs/Claude_Context_Handover_and_Project_Status.md` (Updated to cite authenticated `results/e0_real.jsonl` and exact Table 8.1 measured latencies)
  - `docs/Project_Execution_Roadmap_and_Next_Steps.md` (Updated E0 citation to `results/e0_real.jsonl`)
- **Key Evidence Link**: [`docs/evidence_ledger.md`](../../docs/evidence_ledger.md)

---

## 2026-11-10 - Task N3 Monotone Learned Escalation (Combined Guard G3+G5)
- **Author**: Antigravity Assistant
- **Summary**: Implemented tabular risk models (Logistic Regression and Gradient Boosted Decision Trees) trained on 27-dimensional AST effect features from `effects.analyse`. Evaluated performance across training set sizes (50, 100, 300, 1000) under Out-of-Distribution technique-family splits and chronological time splits. Formulated and proved the Monotone Risk Escalation Principle ($T_{comb} = \max(T_{det}, T_{learned})$), guaranteeing that machine learning can escalate scrutiny but can never lower a deterministic safety tier. Evaluated adversarial false-negative resilience, calibration (ECE), and confirmation burden per 100 benign commands.
- **Files Created/Modified**:
  - `scripts/run_learned_escalation.py` (Feature extractor, StandardScaler, LogisticRegression, GradientBoosting, evaluation metrics, and combined guard G3+G5)
  - `results/learned_escalation.jsonl` (16 authenticated trial records with full 10-field provenance schema, 100% VALID in evidence ledger)
  - `results/learned_escalation_summary.md` (Comprehensive academic evaluation report with OOD performance, ECE calibration, and confirmation tradeoffs)
  - `tests/test_monotone_escalation.py` (10 unit and integration tests verifying feature extraction, model convergence, metrics, adversarial false-negative resilience, and strict monotone invariant preservation)
- **Key Evidence Link**: [`results/learned_escalation_summary.md`](../../results/learned_escalation_summary.md)

---

## 2026-11-09 - Frozen Sealed Test Benchmark Run (Git SHA: dfce40e5)
- **Author**: `scripts/run_sealed.py` (Automated Gate)
- **Timestamp**: `2026-11-09T19:06:34.497628+00:00`
- **Git SHA**: `dfce40e50ae122bbf342a84ccbef0a6eaab4bfaf`
- **Dataset Split**: `datasets/e5_sealed.jsonl` (N=50) + `datasets/safety_v3_sealed.jsonl` (N=100)
- **Key Evidence Link**: [`D:\Final yr Proj\results\sealed_final_summary.md`](../../results/sealed_final_summary.md)

---

## 2026-11-09 - T37/N6 Hardware Matrix Benchmark & Install Autotuner
- **Author**: Antigravity Assistant
- **Summary**: Implemented automated first-run hardware profiler and parameter tuner exploring candidate settings (threads, GPU layers, context size, batch size) under a strict 90-second time budget with safe CPU fallback. Evaluated cross-tier hardware matrix across CPU, Vulkan, and CUDA tiers, demonstrating 24.5 ms TTFT (4.9x speedup) on CUDA and viable 120.0 ms interactive prefill on pure CPU.
- **Files Created/Modified**:
  - `src/nlagent/runtime/autotune.py` (Candidate grid generator, fixed prompt evaluator, TTFT/stability profiler, 90s time cap enforcer, and profile persistence)
  - `src/nlagent/runtime/__init__.py` (Exported autotune symbols and resolved circular import)
  - `src/nlagent/runtime/manager.py` (Eliminated circular dependency with provenance)
  - `scripts/run_hardware_matrix.py` (Evaluates CPU, Vulkan, and CUDA tiers, logging to JSONL with full 10-field provenance schema)
  - `results/hardware_matrix.jsonl` (Cross-tier performance records with full provenance metadata)
  - `results/hardware_matrix_summary.md` (Comparative latency, throughput, VRAM, and power efficiency report)
  - `tests/test_autotune.py` (8 unit and integration tests covering grid generation, candidate evaluation, time capping, crash fallback, and profile persistence)
- **Key Evidence Link**: [`results/hardware_matrix_summary.md`](../../results/hardware_matrix_summary.md)

---

## 2026-11-09 - T42 Adaptive Adversarial Attacker & Multi-Stage Evasions
- **Author**: Antigravity Assistant
- **Summary**: Implemented adaptive multi-stage evasion generator against the combined G0-G3 guard pipeline across PowerShell, cmd.exe, and Bash. Evaluated 816 combinatorial trials (544 attacks, 272 benign controls) spanning stages 0 to 4. Computed exact Clopper-Pearson 95% confidence intervals demonstrating 98.5% interception rate (1.5% bypass rate) for the combined multi-tier pipeline versus 71.5% bypass on static regex denylists.
- **Files Created/Modified**:
  - `scripts/run_adaptive_attacker.py` (Multi-stage evasion engine generating atomic and chained mutations, evaluating G0-G3 guards, and computing Clopper-Pearson intervals)
  - `results/adaptive_attacks.jsonl` (816 evaluation records with full 10-field provenance schema)
  - `results/adaptive_attacks_summary.md` (Performance matrix, stage-depth breakdown, per-shell statistics, and security insights)
  - `tests/test_adaptive_attacker.py` (8 unit and integration tests covering atomic mutators, multi-stage composition, guard evaluations, confidence intervals, and jsonl logging)
- **Key Evidence Link**: [`results/adaptive_attacks_summary.md`](../../results/adaptive_attacks_summary.md)

---

## 2026-11-09 - N4 Autoresearch Bounded Optimization Loops & Audited Sealed Gate
- **Author**: Antigravity Assistant
- **Summary**: Implemented autonomous Karpathy-style optimization loop runner driven by program.md contracts with confinement boundary enforcement, dev-only evaluation, paired bootstrap confidence intervals, and append-only TSV ledgers. Added audited sealed benchmark execution gate with Git SHA freeze tracking, duplicate warning detection, and provenance logging.
- **Files Created/Modified**:
  - `experiments/loops/prompt_opt/program.md` (Authoritative contract specifying objective, single mutable file, dev split, 95% paired bootstrap decision rule, and strict sealed set prohibition)
  - `scripts/run_loop.py` (Autoresearch engine enforcing single-file confinement, paired bootstrap interval calculation, git commit/revert mechanics, and results.tsv tracking)
  - `scripts/run_sealed.py` (Audited execution gate running sealed_test.jsonl once per Git SHA with audit logging in results/sealed_audit.jsonl and vault notice generation)
  - `tests/test_loop_runner.py` (9 unit and integration tests covering contract invariants, sealed set refusal, confinement checks, paired bootstrap decisions, ledger tracking, and sealed test gating)
  - `experiments/loops/prompt_opt/results.tsv` (Append-only experiment ledger tracking trial metrics, deltas, confidence intervals, token counts, and decisions)
- **Key Evidence Link**: [`experiments/loops/prompt_opt/results.tsv`](../../experiments/loops/prompt_opt/results.tsv)

---

## 2026-11-09 - T25/T40 Live E5 Baselines on Dev Split
- **Author**: Antigravity Assistant
- **Summary**: Updated E5 benchmark runner to evaluate live Nemotron-3-Nano model against base/zero-shot prompts strictly on dev split (N >= 100). Implemented strict dev split validation, mock backend exclusion by default, exact McNemar paired significance testing, and paired difference accuracy intervals with full provenance.
- **Files Created/Modified**:
  - `scripts/run_e5.py` (Added split partition governance, minimum sample size validation, mock exclusion enforcement, exact McNemar and paired difference intervals)
  - `datasets/dev_v3.jsonl` (Dev benchmark split containing 100 diverse cases across Bash, PowerShell, and Cmd)
  - `scripts/make_dev_v3.py` (Dev split generator with isolated fixture specifications)
  - `tests/test_run_e5.py` (15 unit tests verifying split enforcement, mock refusal, paired interval math, and e2e execution)
  - `results/e5_dev.jsonl` (400 per-case evaluation records with full provenance schema)
  - `results/e5_dev_summary.md` (Summary performance matrix, McNemar paired comparisons, paired difference intervals, and provenance block)
- **Key Evidence Link**: [`results/e5_dev_summary.md`](../../results/e5_dev_summary.md)

---

## 2026-11-09 - N2b Grounding Integration & Empirical Context Evaluation
- **Author**: Antigravity Assistant
- **Summary**: Integrated bounded filesystem grounding and profile into propose() as an optional FILES block preserving L0/L1 prefix stability. Built context evaluation benchmark measuring path hallucination and reference resolution across 3 architectures with Wilson intervals.
- **Files Created/Modified**:
  - `src/nlagent/daemon/propose.py` (Integrated FILES block into L2 after compact STATE, byte-identical L0/L1)
  - `scripts/run_context_eval.py` (Benchmark script measuring hallucination, reference resolution, token counts, and latency)
  - `datasets/context_eval_cases.jsonl` (Multi-turn evaluation dataset with 20 scenarios / 40 turns)
  - `tests/test_injection.py` (Immunity verification against hostile filename injections, tier spoofing, and boundary breakouts)
  - `results/context_eval.jsonl` (Line-by-line evaluation logs)
  - `results/context_eval_summary.md` (Summary table with Wilson score and bootstrap intervals)
- **Key Evidence Link**: [`results/context_eval_summary.md`](../../results/context_eval_summary.md)

---

## 2026-11-09 - T32 Deterministic Plain-English Consequence Renderer
- **Author**: Antigravity Assistant
- **Summary**: Implemented deterministic plain-English consequence generator translating AST effects into non-technical descriptions with mandatory Cancel default for destructive tiers.
- **Files Created/Modified**:
  - `src/nlagent/safety/renderer.py` (EffectSummary, ExecutionPlan, human byte formatting, directory scope summarization, full preview banners)
  - `src/nlagent/safety/__init__.py` (Package exports)
  - `tests/test_renderer.py` (18 unit tests covering wildcards, drive roots, bulk deletions, overwrite warnings, and default action enforcement)
- **Key Evidence Link**: [`tests/test_renderer.py`](../../tests/test_renderer.py)

---

## 2026-11-09 - N2a Context Grounding & Filename Sanitizer
- **Author**: Antigravity Assistant
- **Summary**: Implemented bounded read-only filesystem grounding, user profile with approve-before-save governance, and hostile filename injection sanitizer.
- **Files Created/Modified**:
  - `src/nlagent/context/grounding.py` (Bounded read-only search by name, extension, mtime; strict folder confinement; top-K limit)
  - `src/nlagent/context/profile.py` (UserProfile aliases, favourite folders, approve-before-save suggestion pipeline, forget-all)
  - `src/nlagent/context/sanitize.py` (Hostile filename injection defense, control character stripping, XML delimiter wrapping)
  - `src/nlagent/context/__init__.py` (Package exports)
  - `tests/test_grounding.py` (18 comprehensive tests covering search, profile governance, and hostile filename injections)
- **Key Evidence Link**: [`tests/test_grounding.py`](../../tests/test_grounding.py)

---

## 2026-11-09 - T31 Core Service Daemon & API Integration
- **Author**: Antigravity Assistant
- **Summary**: Implemented local background HTTP service running on loopback with ephemeral bearer token authentication and seamless in-process fallback for CLI and hooks.
- **Files Created/Modified**:
  - `src/nlagent/service/core.py` (ServiceInfo registry, ServiceDaemon lifecycle, ServiceClient fallback)
  - `src/nlagent/service/api.py` (ServiceAPI dispatcher for `/propose`, `/commit`, `/undo`, `/models`, `/health`)
  - `src/nlagent/service/__init__.py` (Service module exports)
  - `src/nlagent/cli/main.py` (`nlctl service {start,stop,status}`, service IPC query in propose/commit/undo)
  - `src/nlagent/cli/quick_propose.py` (Shell hook IPC query with fallback)
  - `src/nlagent/cli/db.py` (Sandboxed database resolution with permission probe fallback)
  - `tests/test_service_api.py` (Integration test suite for service daemon and in-process fallback)
- **Key Evidence Link**: [`tests/test_service_api.py`](../../tests/test_service_api.py)

---

## 2026-11-09 - N1 Dev Vault & Provenance Memory Initialization
- **Author**: Antigravity Assistant
- **Summary**: Initialized the research knowledge vault structure following the Karpathy LLM-wiki / Obsidian vault pattern.
- **Files Created**:
  - `vault/AGENTS.md` (Vault governance & agent reading hierarchy)
  - `vault/raw/README.md` (Raw source isolation guidelines)
  - `vault/wiki/hot.md` (Active working context and milestone state)
  - `vault/wiki/index.md` (Master knowledge catalog)
  - `vault/wiki/log.md` (Append-only changelog)
  - `vault/wiki/decisions/D01_standalone_llama_server.md` (Decision record for bundled server)
  - `vault/wiki/decisions/D02_windows_native_safety.md` (Decision record for native AST parsing)
  - `vault/wiki/experiments/E0_prefix_cache_latency.md` (Experiment card for prefix caching)
  - `vault/wiki/experiments/E_safety_bench_v3.md` (Experiment card for Benchmark v3)
  - `vault/wiki/claims/C1_low_latency_cpu.md` (Claim ladder for low-resource latency)
  - `vault/wiki/claims/C2_zero_unconfirmed_harm.md` (Claim ladder for safety guarantees)
  - `vault/wiki/prior_art/open_source_baselines.md` (Prior art comparison notes)
  - `scripts/vault_lint.py` (Automated vault linter)
  - `tests/test_vault_lint.py` (Unit tests for vault linter)
- **Key Evidence Link**: [`vault/wiki/index.md`](../../vault/wiki/index.md)
