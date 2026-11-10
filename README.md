# NL-OS-Agent: Safety-Verified AI-Native Operating System Shell Agent

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Test Suite](https://img.shields.io/badge/tests-398%20passed%2C%201%20skipped-brightgreen.svg)]()
[![Sealed Interception](https://img.shields.io/badge/Combined%20G0--G3%20interception-98.3%25-brightgreen.svg)]()
[![Tuned Short Accuracy](https://img.shields.io/badge/E5%20Tuned--Short-94.0%25%20%40%205.0x%20TTFT-success.svg)]()
[![Architecture](https://img.shields.io/badge/pipeline-two--phase%20propose%2Fcommit-orange.svg)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**NL-OS-Agent** is a standalone, safety-verified natural language interface for Windows PowerShell, `cmd.exe`, and POSIX (`bash`/`zsh`) terminals. Powered by a context-distilled local language model (**NVIDIA Nemotron-3-Nano-4B Q4_K_M** via supervised `llama-server` without LM Studio), it pairs neural command synthesis with a **formal two-phase `propose()` / `commit()` pipeline**, **cross-shell AST static effect analysis**, **pre-execution TOCTOU verification**, **hardlink quarantine rollback**, **copy-on-write multi-step virtual overlays**, and a **PySide6 Desktop UI & Usability Study Suite**.

---

## 1. Core Architectural Pillars

1. **Two-Phase `propose()` $\to$ `commit()` Invariant** ([`propose.py`](src/nlagent/daemon/propose.py), [`commit.py`](src/nlagent/daemon/commit.py)):  
   The LLM can *only* propose structured JSON payloads (`command`, `targets`, `explanation`, optional `clarifying_question`); it never invokes OS execution directly. `commit()` re-analyzes the live filesystem at the moment of execution to block **Time-Of-Check to Time-Of-Use (TOCTOU)** race mutations (`exit code 126`).
2. **Cross-Shell Static Effect Engine & 5 Deterministic Risk Tiers** ([`effects.py`](src/nlagent/safety/effects.py), [`ps_parser.py`](src/nlagent/safety/ps_parser.py), [`cmd_parser.py`](src/nlagent/safety/cmd_parser.py)):  
   Decomposes PowerShell cmdlets/pipelines, `cmd.exe` verbs/switches, and POSIX commands statically into `read_only`, `state_modifying`, `destructive`, `privileged`, and `ambiguous` tiers.
3. **Plain-English Consequence Renderer & Cancel-Safe Default** ([`renderer.py`](src/nlagent/safety/renderer.py)):  
   Converts technical AST effects into non-technical summaries (affected counts, byte sizes, directory scopes, wildcard warnings, and Quarantine reversibility status). Enforces **Cancel** as the default focused button/action for destructive, privileged, or ambiguous operations.
4. **Shadow Quarantine & Byte-Exact Undo** ([`quarantine.py`](src/nlagent/safety/quarantine.py)):  
   Automatically shadows deleted, overwritten, or appended files/directories into local quarantine before execution (`nlctl undo` restores byte-identical contents with SHA-1 verification).
5. **Copy-on-Write Virtual Filesystem Overlay & Multi-Step Plans** ([`overlay.py`](src/nlagent/safety/overlay.py), [`plan/engine.py`](src/nlagent/plan/engine.py)):  
   Supports up to 10-step workflows (`MAX_PLAN_STEPS = 10`) where step $k$'s effects and wildcards are simulated on the in-memory overlay of steps $1 \dots k-1$, compiling one combined preview and one transactional `UndoGroup` with automatic rollback on failure.
6. **Grounding & Hostile Filename Injection (HFI) Sanitizer** ([`grounding.py`](src/nlagent/context/grounding.py), [`sanitize.py`](src/nlagent/context/sanitize.py), [`profile.py`](src/nlagent/context/profile.py)):  
   Bounded, metadata-only file search injects real candidate paths while stripping control characters and delimiting untrusted filenames so disk names containing shell injection payloads cannot alter tiering or prompt instructions.
7. **Standalone Runtime, Core Loopback Service & Desktop UI** ([`runtime/manager.py`](src/nlagent/runtime/manager.py), [`service/core.py`](src/nlagent/service/core.py), [`ui/app.py`](src/nlagent/ui/app.py)):  
   Manages precompiled CUDA/Vulkan/CPU `llama-server.exe` binaries with GPU-to-CPU fallback, an install-time 90s autotuner ([`autotune.py`](src/nlagent/runtime/autotune.py)), an authenticated `127.0.0.1` HTTP daemon (`nlctl service start`), PySide6 Floating Preview Window, System Tray icon, First-Run Setup Wizard, air-gapped Usability Study Mode, and Windows Inno Setup packaging ([`setup.iss`](installer/setup.iss)).

---

## 2. Repository Structure

```text
nl-os-agent/
├── README.md                          # Project overview, setup, live execution & verification guide
├── pyproject.toml                     # Package config & nlctl / nlagent console entrypoints
├── pytest.ini                         # Pytest configuration
├── agent.py                           # Interactive Rich shell agent demo (Propose / Commit REPL)
├── nlctl.cmd / nlagent.cmd            # Windows CLI wrappers
│
├── src/nlagent/                       # Core Python package
│   ├── cli/                           # CLI (propose, commit, undo, history, doctor, service) & SQLite store
│   ├── service/                       # Authenticated loopback HTTP daemon & fallback client
│   ├── runtime/                       # Hardware detection, llama-server manager, model store & autotuner
│   ├── backends/                      # OpenAI/llama-server client with chat template & prompt cache support
│   ├── daemon/                        # Two-stage propose() & commit() execution pipelines
│   ├── safety/                        # Effect analyser, PowerShell/cmd AST parsers, renderer, overlay & quarantine
│   ├── plan/                          # Up-to-10-step multi-step plan engine & atomic UndoGroup
│   ├── context/                       # 3-layer L0/L1/L2 prompt layout, grounding, profile & HFI sanitizer
│   ├── cache/                         # Prefix cache & semantic verified-command cache
│   ├── ui/                            # PySide6 tray, floating preview, wizard, consent & study logger
│   ├── bench/                         # Provenance validator, stats (Wilson/McNemar/Clopper-Pearson) & E0 harness
│   └── eval/                          # Sandboxed manifest comparison execution evaluator
│
├── shell/                             # Native terminal Ctrl+G bindings (nl-bind.ps1, nl-bind.bash)
├── models/                            # GGUF manifest & license notice (local weights stored untracked)
├── installer/                         # Windows Inno Setup installer script (setup.iss)
├── datasets/                          # Dev, attack, and sealed benchmark partitions
├── results/                           # Authenticated evaluation JSONL outputs & Markdown summaries
├── docs/                              # Evidence ledger (docs/evidence_ledger.md) & milestone status docs
├── paper/                             # Academic LaTeX draft (paper/main.tex)
├── vault/                             # Structured provenance memory vault & wiki logs
├── experiments/loops/                 # Autonomous prompt/runtime optimization loop contracts
├── scripts/                           # Benchmarks, figure generators, paper numbers gate & vault linter
├── tests/                             # 398-test automated pytest suite
└── Ref_docs/                          # Archived planning, handover, slide, report & testing reference docs
```

---

## 3. Standalone Installation & Live Setup (No LM Studio Needed)

### Step 1: Install the Package in Editable Mode
Requires Python 3.10+ (verified on Python 3.11.9 / Windows 11):
```powershell
pip install -e .
```
*(If PySide6/UI or ML evaluation scripts are used, ensure `PySide6`, `requests`, `rich`, `numpy`, and `scikit-learn` are available in your environment.)*

### Step 2: Provide Standalone `llama-server.exe` + GGUF Weights
1. Download the official Windows release archive matching your GPU/CPU (`llama-*-bin-win-cuda-12.x-x64.zip` and `cudart-llama-*-bin-win-cuda-12.x-x64.zip` for NVIDIA CUDA, or Vulkan/CPU builds) from [ggml-org/llama.cpp Releases](https://github.com/ggml-org/llama.cpp/releases), and extract them into `bin\` (creating `bin\llama-server.exe`).
2. Place your quantized dense GGUF model (e.g., `NVIDIA-Nemotron-3-Nano-4B-Dense-Q4_K_M.gguf`) in `models\`.  
   *(If converting directly from an Unsloth export hitting `blk.1.ssm_in.weight`, repair metadata once with `python scripts/fix_nemotron_gguf.py --input <raw.gguf> --output models/NVIDIA-Nemotron-3-Nano-4B-Dense-Q4_K_M.gguf`.)*
3. Verify hardware detection, binary resolution, and model integrity:
   ```powershell
   nlctl doctor --model "models/NVIDIA-Nemotron-3-Nano-4B-Dense-Q4_K_M.gguf"
   ```

### Step 3: Start `llama-server` & Launch NL-OS-Agent
**Terminal 1** (keep running as the inference backend):
```powershell
.\bin\llama-server.exe -m "models\NVIDIA-Nemotron-3-Nano-4B-Dense-Q4_K_M.gguf" --host 127.0.0.1 --port 8080 -ngl 33 -c 2048 -t 4
```
*(Note: Do **not** type shell requests into the browser page at `http://127.0.0.1:8080/` — that is llama.cpp's raw model chat page. Use any of the NL-OS-Agent clients in Terminal 2 below.)*

**Terminal 2** (launch any NL-OS-Agent client; set `$env:NLAGENT_REASONING = "0"` for fast single-pass inference):
```powershell
$env:NLAGENT_REASONING = "0"

# 1. Interactive Terminal REPL Demo
python agent.py

# 2. Guided 7-Stage Acceptance Walkthrough (Read-only, Create, Ambiguous, Destructive, Undo, Blocked, Cache)
python scripts/demo.py

# 3. CLI Subcommands (inside any sandbox workspace)
nlctl propose "list python files in src"
nlctl commit <proposal_id>
nlctl undo <proposal_id>
nlctl history

# 4. Desktop PySide6 UI, Setup Wizard & Usability Study Mode
python -m nlagent.ui.app --propose "show all hidden files in current folder"
python -m nlagent.ui.app --wizard
python -m nlagent.ui.app --study
python -m nlagent.ui.app

# 5. Inline Shell Hotkey (Ctrl+G in PowerShell or Bash)
. "shell\nl-bind.ps1"      # Windows PowerShell
# source shell/nl-bind.bash  # Linux / macOS
```

---

## 4. Key Sealed & Empirical Benchmark Highlights

All reported metrics are provenance-tracked in [`docs/evidence_ledger.md`](docs/evidence_ledger.md) and gated against drift via `python scripts/gate_paper_numbers.py`:

- **Sealed E5 Prompt Compression Evaluation** ([`results/sealed_final_summary.md`](results/sealed_final_summary.md)):
  - **Tuned + Short Prompt**: **94.0%** execution accuracy (95% Wilson CI: `[83.8%, 97.9%]`), **100.0%** JSON schema validity, **100.0%** clarification accuracy, and **24.6 ms** mean TTFT (**5.0x speedup** vs. 122.3 ms full prompt).
  - **Base + Short Prompt**: **32.0%** execution accuracy (`p < 10^-6` exact McNemar test vs. Tuned + Short).
- **Sealed Safety Guard Robustness (`safety_v3_sealed.jsonl`, N=100)** ([`results/sealed_final_summary.md`](results/sealed_final_summary.md)):
  - **Combined G0–G3 Defense Pipeline**: **98.3%** harmful evasion interception (95% Clopper-Pearson CI: `[91.1%, 100.0%]`) across multi-stage PowerShell, `cmd.exe`, and Bash attacks (vs. **40.0%** on naive G0-simple regex).
- **Cross-Tier Hardware Matrix** ([`results/hardware_matrix_summary.md`](results/hardware_matrix_summary.md)):
  - Offloading 33/33 layers on NVIDIA GeForce RTX 2050 CUDA achieves **25.4 ms** median TTFT (**4.7x** vs. CPU baseline).

---

## 5. Verification & Integrity Checks

Run the complete test suite, vault integrity linter, and paper numbers concordance gate locally inside isolated temporary directories:
```powershell
# Run full test suite (398 passed, 1 skipped)
python -m pytest tests/ -q

# Verify Dev Vault links & ledger provenance rules (0 violations)
python scripts/vault_lint.py

# Verify 100% numeric concordance across docs, paper & ledger (121/121 verified, 0 violations)
python scripts/gate_paper_numbers.py
```

For detailed task specifications, experimental designs, and additional CLI usage examples, see [`Ref_docs/`](Ref_docs/) (including [`TESTING_GUIDE.md`](Ref_docs/TESTING_GUIDE.md) and [`EXECUTION_ROADMAP_AND_PROMPTS.md`](Ref_docs/EXECUTION_ROADMAP_AND_PROMPTS.md)).

---

## License

MIT License. Model weights are governed by their upstream license (`models/NOTICE`).
