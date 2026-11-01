# NL OS Agent: Project Execution Roadmap & Next Steps Guide

This document provides the concrete operational plan for completing the remaining deliverables of the **AI-Native Operating System Shell Agent** project, organized into parallel tracks for fine-tuning, interactive demo integration, and empirical reporting.

---

## 1. System Health Check & Current Baseline

### Problems Panel Status
- **0 Errors / 0 Warnings**: All Python source files (`src/`, `tests/`, `scripts/`, `agent.py`) compile cleanly without type or syntax errors.
- **PyLance / Pyright Configuration**: Project root configured with `extraPaths = ["src", "scripts"]`.

### Test Suite (`pytest -q`)
```text
............................................................             [100%]
60 passed in 0.88s
```
*All 60 unit and integration tests passing cleanly in under 1 second.*

### Repository State
- Branch: `main`
- Origin: `https://github.com/Siva-0317/nl-os-agent.git` (Clean and synchronized)

---

## 2. Where We Stand Today

```
[ Natural Language Request ]
            │
            ▼
┌────────────────────────────────────────────────────────┐
│ Stage 1: propose()                                     │
│  1. Reference Resolution (that file -> /path/to/file)  │
│  2. SessionState.render() (Bounded O(1) state block)   │
│  3. layout.build_prompt() (Deterministic L0/L1/L2)     │
│  4. VerifiedCommandCache Lookup (Cosine sim >= 0.90)   │
│     ├── Hit: Bypass LLM, inherit verified proposal     │
│     └── Miss: LlamaServerBackend (/completion)         │
│  5. JSON Validation & Target Extraction                │
│  6. Static Effect Analysis (effects.analyse)           │
│  7. Tier Determination (tier_from_effect) & Preview    │
│  8. Output: {command, tier, preview, targets, cached}  │
└────────────────────────────────────────────────────────┘
            │
            ▼
┌────────────────────────────────────────────────────────┐
│ Policy / User Confirmation Gate                        │
│  - read_only / state_modifying: Auto-approved          │
│  - destructive / ambiguous: Prompt user [y/N]          │
│  - privileged: Require explicit admin confirmation     │
└────────────────────────────────────────────────────────┘
            │
            ▼
┌────────────────────────────────────────────────────────┐
│ Stage 2: commit()                                      │
│  1. Re-run effects.analyse() (TOCTOU race protection)  │
│  2. Verify safety policy still holds                   │
│  3. Execute in Shell                                   │
│  4. On Exit Code 0:                                    │
│     - Record in VerifiedCommandCache                   │
│     - Update SessionState history & entity table       │
│     - Track 'cd' working directory updates             │
└────────────────────────────────────────────────────────┘
```

---

## 3. Track 1: Fine-Tuning Pipeline (Colab + Unsloth)

### Objective (Experiment E5)
Train and compare two LoRA adapters on top of the 4B base model:
1. **Base Adapter**: Standard instruction-tuned command translation.
2. **Context-Distilled Adapter**: Trained to condition directly on the deterministic L1 session metadata and compact L2 state blocks.

### Step 1: Toolchain Smoke Test (Colab)
1. Open Google Colab and select a **Free T4 GPU** runtime.
2. Use [`scripts/colab_unsloth_finetuning.py`](file:///d:/Final%20yr%20Proj/scripts/colab_unsloth_finetuning.py) as your notebook code.
3. Train with a small 50–100 sample subset (`datasets/finetune_val.jsonl`) for 30 steps.
4. **Validation Goal**: Confirm that Unsloth installs cleanly, 4-bit quantization loads within Colab VRAM (<6 GB), and gradient checkpointing succeeds without CUDA out-of-memory errors.

### Step 2: The Real 8k+ Dataset
The dataset generator [`scripts/make_finetune_dataset.py`](file:///d:/Final%20yr%20Proj/scripts/make_finetune_dataset.py) has generated **8,000 structured examples**:
- **Location**:
  - `datasets/finetune_train.jsonl` (7,200 training examples)
  - `datasets/finetune_val.jsonl` (800 validation examples)
- **Features Covered**:
  - **Template Families**: File operations, process control, git workflows, networking, system monitoring.
  - **Cross-Shell Conditioning**: Bash (POSIX paths) vs. PowerShell (Windows cmdlets and path notations).
  - **Clarification Handling**: Ambiguous inputs emit `{"command": null, "clarifying_question": "..."}` rather than guessing dangerous defaults.

### Step 3: Training & GGUF Export
1. Train the adapter for ~1,200 steps (1–2 epochs on 7,200 examples, ~45 minutes on Colab T4).
2. Export the merged weights as GGUF with 4-bit medium quantization (`Q4_K_M`):
   ```python
   model.save_pretrained_gguf("nemotron_distilled_q4km", tokenizer, quantization_method="q4_k_m")
   ```
3. Download the resulting `.gguf` file to your local machine and point `llama-server` to it.

---

## 4. Track 2: Interactive Demo & Shell Hook Integration

### The Problem Solved
Currently, executing commands via Python `subprocess.run()` starts a subshell, meaning `cd <dir>` and `export VAR=val` do not persist in the user's terminal session. 

### How `propose()` and `commit()` Solve This
By decoupling proposal from execution, the approved command can be **evaluated by the user's real shell**:

#### Bash Hook Setup (`~/.bashrc`):
```bash
# NL OS Agent Bash Keybinding (Ctrl+G)
_nl_agent_hook() {
    local cmd
    cmd=$(python -m nlagent.cli.quick_propose "$READLINE_LINE" "$PWD")
    if [ -n "$cmd" ]; then
        READLINE_LINE="$cmd"
        READLINE_POINT=${#READLINE_LINE}
    fi
}
bind -x '"\C-g": _nl_agent_hook'
```

#### PowerShell Hook Setup (`$PROFILE`):
```powershell
Set-PSReadLineKeyHandler -Chord 'Ctrl+g' -ScriptBlock {
    $line = ""
    $cursor = 0
    [Microsoft.PowerShell.PSConsoleReadLine]::GetBufferState([ref]$line, [ref]$cursor)
    $proposed = python -m nlagent.cli.quick_propose $line (Get-Location).Path
    if ($proposed) {
        [Microsoft.PowerShell.PSConsoleReadLine]::Replace(0, $line.Length, $proposed)
    }
}
```

### Scope Clarification
- **Core Requirement**: A responsive shell hook demo where the user presses `Ctrl+G`, the natural language is converted to an approved command in the terminal buffer, and pressing Enter executes it directly in their native shell.
- **Optional**: Voice transcription and Admin elevation brokers remain documented Phase-II stretch goals.

---

## 5. Sharpening the Research Novelty & Academic Rigor

When writing the project report and paper, emphasize these four positioning pillars:

### 1. Caching and Safety Bound Together (Novel Contribution)
Most LLM caching literature focuses purely on token latency and assumes deterministic output. Our work evaluates:
- **Greedy Output Identity (E0)**: 100% character-by-character output equality between cached and uncached runs across 50 multi-turn prompts (`results/e0.jsonl`).
- **Safety Decision Invariance**: Proof that verified semantic caching (`VerifiedCommandCache`) never bypasses static safety analysis; every cache hit is re-verified by `effects.analyse`.

### 2. Honest Failure Mode Analysis of the Static Effect Engine
Admitting where static analysis reaches theoretical limits signals academic rigor:
- **Opaque Commands**: Eval strings (`eval $(...)`), base64 pipes (`base64 -d | sh`), and inline code (`python -c "..."`) cannot be fully simulated statically and are honestly tiered as `AMBIGUOUS`.
- **External Side Effects**: Network operations (`curl`, `git clone`, `docker`) cannot be rolled back or simulated offline without sandbox containment.

### 3. Reporting Negative Results (KV-Cache Quantization)
- If quantizing the KV cache to 4-bit (`--cache-type-k q4_0`) causes quality degradation or provides negligible speedup on small context windows (<1,000 tokens), document it as a **rigorous negative finding**.
- Explaining *why* (FP16/FP32 KV cache overhead for 4B models at 512 tokens is only ~32 MB) shows deep systems understanding.

### 4. Realistic, Defensible Claims
- **Avoid**: *"We built the first prefix-cache architecture for hybrid recurrent models."* (Community forks of llama.cpp are actively patching this).
- **Claim Instead**: *"An application-aware, safety-gated execution pipeline for local 4B shell agents operating on consumer 4 GB GPUs, coupling semantic verified caching with pre-execution effect tiering."*

---

## 6. Step 1 Measurement List: What to Run and Report

### 1. `llama-bench` Baseline Latency
Run `llama-bench` on your local llama.cpp installation to obtain exact prompt processing ($t_{\text{pp}}$) and generation ($t_{\text{tg}}$) speeds:

```powershell
llama-bench -m <path-to-nemotron-or-qwen-q4.gguf> -p 64,128,256,512 -n 64 -ngl 99
```

**Key Numbers to Report**:
- Prompt evaluation speed (`t/s`) for 128 and 512 tokens.
- Generation speed (`t/s`) for 64 tokens.

### 2. Model Load-Log Buffer Sizes
When starting `llama-server`, observe the initial startup log output in the terminal:

```powershell
llama-server -m <path-to-model.gguf> -c 2048 -ngl 99
```

**Log Lines to Capture**:
- `llama_kv_cache_init: VRAM required = ... MB`
- `llm_load_print_meta: model type = ...`
- `llm_load_tensors: offloaded ... layers to GPU`

These numbers give the exact physical memory overhead of the KV cache buffers for Chapter 8 of your report.

---

## 7. Immediate Action Checklist

1. [x] **Repository Clean & Green**: Single top-level root, 60/60 tests passing, 0 problems.
2. [x] **Two-Stage Architecture**: `propose()` and `commit()` implemented in `src/nlagent/daemon/`.
3. [x] **G0..G3 Safety Benchmark**: 73 cases evaluated, 0.0% G3 bypass rate in `results/safety.jsonl`.
4. [x] **T9 Figures Regenerated**: Tables 8.1, 8.2, 8.3 and Mermaid Figures 8.1, 8.2, 8.3 generated.
5. [x] **8k Fine-Tuning Dataset**: Generated in `datasets/finetune_train.jsonl` and `finetune_val.jsonl`.
6. [ ] **Step 1 (Colab)**: Open Colab, upload `datasets/finetune_val.jsonl`, run [`scripts/colab_unsloth_finetuning.py`](file:///d:/Final%20yr%20Proj/scripts/colab_unsloth_finetuning.py) for a 30-step smoke test.
7. [ ] **Step 2 (Local)**: Start `llama-server` and note the `llama_kv_cache_init` buffer allocation line.
8. [ ] **Step 3 (Demo)**: Wire the thin `quick_propose` CLI helper to test the `Ctrl+G` terminal keybinding.
