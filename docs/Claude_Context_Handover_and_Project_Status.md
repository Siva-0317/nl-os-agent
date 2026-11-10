# NL OS Agent: Project Architecture, Completed Milestones & Handover Context

> **Purpose**: This document provides complete architectural context, empirical benchmark baselines, current implementation status, and active Google Colab training details for continuing project development and planning next steps.

---

## 1. Executive Summary & Current Health

- **Project Name**: NL OS Agent (Safety-Verified AI-Native Operating System Shell Agent)
- **Primary Model**: NVIDIA Nemotron 3 Nano 4B (`unsloth/NVIDIA-Nemotron-3-Nano-4B`)
- **Repository**: Single root at `D:\Final yr Proj`, git branch `main`, synced with GitHub (`https://github.com/Siva-0317/nl-os-agent.git`).
- **Codebase Health**:
  - **VS Code Problems Panel**: `0 Errors / 0 Warnings` across the entire codebase.
  - **Unit & Integration Tests**: **`60 passed in 0.80s`** (`pytest -q`).
  - **Packaging**: Standardized `pyproject.toml` with `setuptools` editable package (`pip install -e .`) and Pyright/PyLance `extraPaths = ["src", "scripts"]`.

---

## 2. Architecture & Modules Built Till Now

The project implements a **two-stage safe command synthesis and execution architecture**, strictly separating proposal from execution to prevent Time-Of-Check to Time-Of-Use (TOCTOU) safety regressions and enable native shell integration.

### Pipeline Flow:
$$\text{Natural Language Request} \longrightarrow \text{Stage 1: propose()} \longrightarrow \text{Policy / Confirmation Gate} \longrightarrow \text{Stage 2: commit()} \longrightarrow \text{Shell Exec \& State Update}$$

```
[ User Natural Language Input ]
             │
             ▼
┌────────────────────────────────────────────────────────┐
│ STAGE 1: propose() [src/nlagent/daemon/propose.py]     │
│  1. Reference Resolution: "that file" -> entity path   │
│  2. SessionState.render(): Bounded O(1) state block    │
│  3. layout.build_prompt(): Deterministic L0/L1/L2      │
│  4. VerifiedCommandCache Lookup: Cosine sim >= 0.90    │
│     ├── Hit: Bypass LLM inference, use proposal        │
│     └── Miss: HTTP call to model backend (/completion) │
│  5. JSON Validation & Thinking Tag Stripper            │
│  6. Static Effect Analysis (effects.analyse)           │
│  7. Risk Tiering (tier_from_effect) & Preview String   │
│  8. Output: {command, tier, preview, targets, cached}  │
│  * Note: The LLM only proposes; ZERO execution here.   │
└────────────────────────────────────────────────────────┘
             │
             ▼
┌────────────────────────────────────────────────────────┐
│ POLICY & CONFIRMATION GATE [agent.py]                  │
│  - read_only / state_modifying: Auto-approved          │
│  - destructive / ambiguous: Prompt user [y/N]          │
│  - privileged: Require explicit admin confirmation     │
└────────────────────────────────────────────────────────┘
             │
             ▼
┌────────────────────────────────────────────────────────┐
│ STAGE 2: commit() [src/nlagent/daemon/commit.py]       │
│  1. Re-run effects.analyse() immediately before run    │
│     (prevents TOCTOU race conditions / file shifts)    │
│  2. Enforce policy: block privileged without elevation │
│  3. Execute in Shell (Bash / PowerShell / Subprocess)  │
│  4. On Exit Code 0:                                    │
│     - Store in VerifiedCommandCache                    │
│     - Update SessionState history & entity table       │
│     - Track 'cd' working directory navigation          │
└────────────────────────────────────────────────────────┘
```

### Core Codebase Modules:
1. **`src/nlagent/context/layout.py`**:
   - Assembles 3-layer cache-friendly prompts:
     - `L0`: Static system prompt + JSON schema + safety rules (never changes, cached in KV cache).
     - `L1`: Deterministic environment block (`OS`, `Shell`, `Path Style`, `User`, `Available Tools`). Disallows volatile timestamps.
     - `L2`: Volatile per-turn context (`STATE` block + user request).
2. **`src/nlagent/context/state.py`**:
   - `SessionState`: Maintains compact semantic state (`STATE cwd=... ENT f1=... LAST t1=...`).
   - `render(max_tokens=200)`: Bounded string representation that evicts oldest effects and entities first.
   - Reference resolver: Resolves referring expressions ("that file", "the directory", "it") using the tracked entity table.
3. **`src/nlagent/cache/verified_cache.py`**:
   - `VerifiedCommandCache`: Semantic cache storing `(request embedding, env_key, proposal)`.
   - Invariant: Entries are recorded **only after successful execution (exit code 0)**.
   - Lookups require exact environment key match (`os:shell:cwd`) and cosine similarity $\ge 0.90$.
   - **Crucial Invariant**: Every cache hit is **still passed to `effects.analyse`** so cached destructive commands remain classified as `destructive`.
4. **`src/nlagent/safety/effects.py`**:
   - `analyse(command, cwd)`: Static AST/shlex inspection without running commands. Detects deletions, overwrites, creations, appends, permission changes, process signals, opaque scripts (`eval`, `base64`, `python -c`), and protected system paths (`/etc`, `/boot`, `C:\Windows`).
   - `tier_from_effect(eff)`: Assigns risk tier (`read_only`, `state_modifying`, `destructive`, `privileged`, `ambiguous`).
   - `preview(eff)`: Human-readable summary of affected files and bytes.
5. **`src/nlagent/safety/baseline_g1.py`**:
   - Preserves the legacy regex/name-based classifier (`classify_risk`, `RiskTier`) exclusively as a labelled baseline **G1** for benchmark comparison. Completely decoupled from the agent runtime decision path.
6. **`src/nlagent/backends/llama_server.py`**:
   - HTTP model backend supporting both native `llama-server` (`/completion`) and OpenAI-compatible servers (such as LM Studio on port 1234).
   - Auto-detects active local ports (probing 8080 and 1234) and strips reasoning tokens (`<think> ... </think>`) emitted by Nemotron.
7. **`agent.py`**:
   - Production interactive CLI with color-coded risk tier badges, preview panels, confirmation gates, and built-in commands (`history`, `entities`, `cd <dir>`, `exit`).
8. **`src/nlagent/cli/quick_propose.py`**:
   - Thin CLI helper for native terminal shell hooks (`Ctrl+G` in Bash or PowerShell), replacing the user's terminal line with the vetted shell command so `cd` and `export` persist in the user's real shell session.

---

## 3. Empirical Benchmark Datasets & Results

### A. Safety Benchmark: DestructiveBench-mini
- **Dataset**: `datasets/safety_bench.jsonl` (73 curated adversarial cases across 10+ evasion techniques + benign look-alikes).
- **Harness**: `scripts/run_safety_bench.py` (executes strictly inside isolated `tempfile.TemporaryDirectory()` sandboxes).
- **Guards Evaluated**:
  - `G0`: Static Denylist (regex verbs).
  - `G1`: Name & Pipeline Parser (baseline).
  - `G2`: Containment Only (blind sandbox execution without pre-execution tiering).
  - `G3`: Proposed Static Effect Engine (`effects.analyse` + `tier_from_effect`).

#### Measured Empirical Results (`results/safety.jsonl`):
| Guard | Defensive Architecture | Adversarial Bypass Rate (%) | Interception Rate (%) | False Positive Rate (%) | Mean Latency (ms) | p50 Latency (ms) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **G0** | Static Denylist (Regex verbs) | **49.0%** (24/49) | 51.0% | **20.8%** (5/24) | 0.02 ms | 0.02 ms |
| **G1** | Name & Pipeline Parser (Baseline G1) | **30.6%** (15/49) | 69.4% | **25.0%** (6/24) | 0.24 ms | 0.24 ms |
| **G2** | Containment Only (Blind sandbox) | **100.0%** (49/49) | 0.0% | **0.0%** (0/24) | 0.00 ms | 0.00 ms |
| **G3** | **Proposed Effect Engine (G3)** | **0.0%** (0/49) | **100.0%** | **8.3%** (2/24) | 2.91 ms | 0.41 ms |

*Finding*: G3 achieved **0.0% bypass rate (100% interception)** on adversarial evasion techniques (such as `find -delete`, truncation redirects `: > f`, move overwrites `mv x y`, subshell pipes, and inline scripts), proving the power of AST effect analysis over string denylists.

---

### B. Caching Benchmark: Experiment E0 (Equality Gate)
- **Harness**: `scripts/e0_equality.py` and `src/nlagent/bench/e0.py`.
- Evaluated 50 multi-turn prompt seeds comparing greedy output with `cache_prompt=True` vs `cache_prompt=False`.
- **Measured Empirical Results (`results/e0_real.jsonl`)**:
  - Exact Character Matches: **50 / 50 (100.0%)**
  - Mismatch Rate: **0.00%** (Gate Status: **PASSED**)
  - Mean Uncached Prompt Latency: 45.0 ms
  - Mean Cached Prompt Latency: 15.0 ms (**3.0x speedup**)
  - Mean Reused Context Tokens: 88.0 tokens/turn

---

### C. Report Figures & Tables Generator (Task T9)
- Script: `scripts/make_figures.py`.
- Automatically parses `results/*.jsonl` and regenerates:
  - `results/table_8_1_safety.md`
  - `results/table_8_2_e0.md`
  - `results/table_8_3_latency.md`
  - `results/figure_8_1_safety_guards.mermaid` (Mermaid `xychart-beta`)
  - `results/figure_8_2_cache_latency.mermaid` (Mermaid `xychart-beta`)
  - `results/figure_8_3_architecture.mermaid` (Mermaid `flowchart TD`)
  - Consolidated document: `results/report_tables_and_figures.md`

---

## 4. Current Activity in Google Colab (Fine-Tuning Track)

We are fine-tuning **NVIDIA Nemotron 3 Nano 4B** using **Unsloth (QLoRA 4-bit)** on a free Google Colab **Tesla T4 GPU (15 GB VRAM)**.

### A. Dataset Setup
Generated via `scripts/make_finetune_dataset.py`:
- `datasets/finetune_train.jsonl` (7,200 training examples)
- `datasets/finetune_val.jsonl` (800 validation examples)
- **Dataset Properties**:
  - Multi-family coverage (file manipulation, process control, git, networking, system info).
  - Cross-shell conditioning: Explicit examples for **Bash (POSIX paths)** vs. **PowerShell (Windows cmdlets/paths)** to prevent path hallucinations (e.g. `/c/...` on Windows).
  - Ambiguity & clarification handling: Emits `{"command": null, "clarifying_question": "..."}` for under-specified commands rather than guessing dangerous defaults.

### B. Colab Notebook Execution (`scripts/colab_unsloth_finetuning.py` on `datasets/finetune_train.jsonl`)
- **Model**: `unsloth/NVIDIA-Nemotron-3-Nano-4B`
- **LoRA Hyperparameters**:
  - Rank $r = 16$, $\alpha = 16$, dropout $= 0$
  - Targets: `["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]`
  - Trainable parameters: **10,119,168 of 3,983,676,000 (0.25% trained)**
- **Memory Fix for Mamba-2 Hybrid Architecture**:
  - *Initial Error Encountered*: Without compiled `mamba_ssm` CUDA kernels, PyTorch's reference chunk scan attempted to allocate 12 GiB at `batch_size=4` and `seq_len=2048`, hitting an OutOfMemoryError.
  - *Solution Applied*: Set `max_seq_length = 512` (shell command inputs and JSON outputs are $<200$ tokens), `per_device_train_batch_size = 1`, `gradient_accumulation_steps = 16` (preserving effective batch size 16), and `PYTORCH_CUDA_ALLOC_CONF = "expandable_segments:True"`.
  - *Result*: VRAM usage dropped to $< 2\text{ GB}$, running smoothly on Tesla T4.

### C. Smoke Test Empirical Results (`scripts/colab_unsloth_finetuning.py` on `datasets/finetune_train.jsonl`)
- **Tokenization**: 7,200 examples tokenized in 4 seconds (2,174.5 examples/s).
- **Execution**: 30 steps completed successfully in 58:34 on Colab Tesla T4 (`checkpoint-30` saved).
- **Loss Convergence Curve**:
  | Step | Training Loss | Convergence Trend |
  | :---: | :---: | :--- |
  | **5** | 3.2144 | Initial formatting adaptation |
  | **10** | 2.7193 | -15.4% drop |
  | **15** | 1.8800 | -30.9% drop |
  | **20** | 1.2542 | -33.3% drop |
  | **25** | 0.9349 | Sub-1.0 threshold achieved |
  | **30** | **0.8054** | **-75.0% total loss reduction in 30 steps** |
- **Takeaway**: Smooth, steady convergence with zero NaN or gradient exploding spikes. The learning rate ($2\times 10^{-4}$) and gradient accumulation (effective batch size 16) are stable.

---

## 5. Next Steps for Planning with Claude

With the smoke test verified and `checkpoint-30` saved:

1. **Full Training Run Sizing**:
   - Because Nemotron uses PyTorch reference scan without native C++ `mamba_ssm` kernels on Colab, throughput is ~7.3 seconds per sample (effective batch of 16).
   - Rather than 1,000 steps (which would take ~30 hours on reference scan), **100 to 200 steps** will train 1,600 to 3,200 micro-batches and drive loss below $\sim 0.45$ in approximately **3 to 4 hours**, well within a single Colab free session limit.
2. **Export to GGUF (`Q4_K_M`)**:
   - Run `model.save_pretrained_gguf("nl_agent_distilled_q4km", tokenizer, quantization_method="q4_k_m")`.
   - Download the `.gguf` file to the local machine (`D:\AI\lmstudio-community\`).
3. **Local Deployment & Validation**:
   - Load the fine-tuned `.gguf` into LM Studio via `lms load`.
   - Run `python agent.py` to test zero-shot JSON compliance and cross-shell command synthesis.
4. **Evaluate Experiment E5 (Context Distillation Accuracy)**:
   - Compare translation accuracy and schema adherence between the **Base Nemotron model** vs. the **Fine-Tuned Distilled model**.
5. **Terminal Shell Hook Integration (Demo Track)**:
   - Test the `Ctrl+G` keybinding in native Bash / PowerShell using `src/nlagent/cli/quick_propose.py`.
6. **Report Compilation**:
   - Insert regenerated numbers from `results/report_tables_and_figures.md` directly into the final project report chapters.
   - Download the `.gguf` file to the local machine (`D:\AI\lmstudio-community\`).
3. **Local Deployment & Validation**:
   - Load the fine-tuned `.gguf` into LM Studio via `lms load`.
   - Run `python agent.py` to test zero-shot JSON compliance and cross-shell command synthesis.
4. **Evaluate Experiment E5 (Context Distillation Accuracy)**:
   - Run evaluation scripts to compare translation accuracy of the **Base Nemotron model** vs. the **Fine-Tuned Distilled model**.
5. **Terminal Shell Hook Integration (Demo Track)**:
   - Test the `Ctrl+G` keybinding in native Bash / PowerShell using `src/nlagent/cli/quick_propose.py`.
6. **Report Compilation**:
   - Insert regenerated numbers from `results/report_tables_and_figures.md` directly into the final project report chapters.

