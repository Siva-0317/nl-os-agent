---
goal: "Optimize system prompt wording in layout.py for execution accuracy and prompt token compression"
mutable_file: "src/nlagent/context/layout.py"
metric: "accuracy_pct"
guard_metric: "mean_prompt_tokens"
guard_threshold: 120.0
dev_dataset: "datasets/dev_v3.jsonl"
eval_command: "python scripts/run_e5.py --split dev"
forbidden_paths:
  - "datasets/sealed_test.jsonl"
  - "src/nlagent/safety"
  - "tests"
  - "scripts/run_loop.py"
  - "scripts/run_sealed.py"
confidence: 0.95
n_resamples: 1000
max_experiments: 10
results_file: "experiments/loops/prompt_opt/results.tsv"
---

# Autoresearch Optimization Loop Contract (`program.md`)

## 1. Objective & Target
- **Goal**: Iteratively optimize system prompt templates (`DEFAULT_SHORT_L0`, `SHORT_L0_PROMPT`) in `src/nlagent/context/layout.py` to maximize end-to-end command execution accuracy on the dev benchmark while minimizing prompt token count and prefill latency.
- **Primary Optimization Metric**: Execution Accuracy (`accuracy_pct`) on `datasets/dev_v3.jsonl`.
- **Secondary Guard Metric**: `mean_prompt_tokens <= 120.0` (prevents prompt bloat) and TTFT prefill latency <= 50.0 ms.

## 2. Governance & Confinement Boundaries
- **Strictly Declared Mutable File**: `src/nlagent/context/layout.py`.
  - The agent is permitted to edit ONLY this declared file.
  - Edits to any other file will trigger an immediate governance violation, aborting the experiment and reverting changes.
- **Strictly Forbidden Surfaces**:
  - **Sealed Test Set**: `datasets/sealed_test.jsonl` is completely sealed. Any attempt to read or reference it terminates the loop immediately.
  - **Safety Pipeline**: `src/nlagent/safety/` is strictly human-reviewed; no autonomous modifications are permitted.
  - **Datasets & Evaluators**: Evaluator scripts and benchmark datasets (`datasets/dev_v3.jsonl`, `scripts/run_loop.py`, `scripts/run_sealed.py`) are immutable.

## 3. Fixed Evaluator Specification
- **Evaluator Command**: `python scripts/run_e5.py --split dev`
- **Evaluation Split**: `datasets/dev_v3.jsonl` ($N \ge 100$ test cases across Bash, PowerShell, and Cmd).
- **Execution Budget**: Maximum 3 minutes per evaluation cycle.

## 4. Acceptance Rule (Paired Bootstrap Significance)
A candidate modification is accepted (KEPT) if and only if:
1. **Statistically Significant Improvement**:
   - The lower bound of the 95% paired bootstrap confidence interval ($\Delta = \text{Accuracy}_{\text{cand}} - \text{Accuracy}_{\text{base}}$) is strictly greater than 0.0 ($CI_{\text{lower}} > 0.0$), **OR**
2. **Pareto Token Reduction (Non-Inferiority)**:
   - Accuracy is non-inferior ($CI_{\text{lower}} \ge -1.0\%$) and prompt tokens are reduced by at least 5.0 tokens without violating the guard threshold (`mean_prompt_tokens <= 120.0`).
3. **Clean Working Tree**:
   - Zero modifications outside `src/nlagent/context/layout.py`.

If these criteria are NOT met, the runner immediately reverts the mutation with Git (`git checkout -- src/nlagent/context/layout.py`).

## 5. Experiment Ledger & Logging
- **Ledger Path**: `experiments/loops/prompt_opt/results.tsv`
- **Recorded Fields**:
  - `experiment_id`: Monotonically increasing trial number (`exp_001`, `exp_002`, ...).
  - `timestamp`: UTC ISO-8601 execution timestamp.
  - `git_sha`: Git commit hash associated with the experiment.
  - `metric_val`: Measured candidate accuracy (%) on `datasets/dev_v3.jsonl`.
  - `baseline_val`: Current best accuracy (%) on `datasets/dev_v3.jsonl`.
  - `delta_pct`: Point estimate difference ($\text{candidate} - \text{baseline}$).
  - `ci_lower`: 95% paired bootstrap lower bound (%).
  - `ci_upper`: 95% paired bootstrap upper bound (%).
  - `prompt_tokens`: Average prompt tokens.
  - `decision`: `KEEP` or `REVERT`.
  - `description`: Summary of the prompt mutation tested.

