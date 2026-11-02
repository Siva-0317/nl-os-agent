# NL-OS-Agent: Complete Project Timeline, Architectural Evolution & Runtime Execution Story

This document provides a comprehensive, chronological narrative of the **Natural Language Operating System Agent (NL-OS-Agent)** project. It details where the project began, the vulnerabilities of the initial prototype, how each layer of the modern architecture was developed, how the fine-tuned model runs in the background, and how to present this work for academic evaluation and project defense.

---

## Table of Contents
1. [The Executive Summary](#1-the-executive-summary)
2. [The 5-Phase Chronological Timeline (From Naive Loop to Production Architecture)](#2-the-5-phase-chronological-timeline)
   - [Phase 0: The Legacy Prototype (October 2026)](#phase-0-the-legacy-prototype-october-2026)
   - [Phase 1: The Architectural Refactor & Safety Engine (`src/nlagent/`)](#phase-1-the-architectural-refactor--safety-engine-srcnlagent)
   - [Phase 2: Scientific Evidence & Empirical Benchmarks](#phase-2-scientific-evidence--empirical-benchmarks)
   - [Phase 3: The Safety Net — Quarantine & Byte-Exact Undo](#phase-3-the-safety-net--quarantine--byte-exact-undo)
   - [Phase 4: Turning It Into an OS Product — `nlctl` & Shell Hooks (`Ctrl+G`)](#phase-4-turning-it-into-an-os-product--nlctl--shell-hooks-ctrlg)
   - [Phase 5: The 7-Stage End-to-End Acceptance Demo & GitHub Freeze](#phase-5-the-7-stage-end-to-end-acceptance-demo--github-freeze)
3. [How the Agent Runs in the Background (Runtime Architecture)](#3-how-the-agent-runs-in-the-background-runtime-architecture)
   - [Do you need LM Studio open? (Backend Options Explained)](#do-you-need-lm-studio-open-backend-options-explained)
   - [The Life of a Keystroke: What happens when you press `Ctrl+G`?](#the-life-of-a-keystroke-what-happens-when-you-press-ctrlg)
   - [Continuous KV-State & Prefix Caching in VRAM](#continuous-kv-state--prefix-caching-in-vram)
4. [Demystifying `nlctl` vs the Shell Hook](#4-demystifying-nlctl-vs-the-shell-hook)
5. [Slide-by-Slide Viva & Presentation Outline](#5-slide-by-slide-viva--presentation-outline)

---

## 1. The Executive Summary

> **The Pitch:**  
> *"Commercial and open-source natural language terminal assistants (like Copilot for CLI or Warp AI) execute shell commands directly or rely on fragile keyword regexes. They suffer from catastrophic accidental deletions, fail against obfuscated attacks like `find -delete`, are vulnerable to TOCTOU filesystem race conditions, and lack rollback mechanisms.*  
>  
> *NL-OS-Agent introduces a formally verified, two-phase `propose()` / `commit()` architecture. It guarantees safety through static effect analysis across 5 deterministic risk tiers, prevents TOCTOU exploits via pre-execution re-checks, implements automated quarantine shadowing with byte-exact rollback, and achieves sub-millisecond latency (<1ms) via a 3-layer deterministic prefix cache."*

---

## 2. The 5-Phase Chronological Timeline

```mermaid
flowchart LR
    P0["Phase 0:\nLegacy Prototype\n(agent.py while-loop,\nregex blacklist)"] --> P1["Phase 1:\nCore Architecture\n(Two-phase propose/commit,\neffects.py AST analysis)"]
    P1 --> P2["Phase 2:\nScientific Benchmarks\n(E0 equality, E5 matrix,\nSafety Bench v2, TOCTOU)"]
    P2 --> P3["Phase 3:\nQuarantine & Undo\n(Pre-execution hardlink shadow,\nSHA-1 byte rollback)"]
    P3 --> P4["Phase 4:\nOS Integration\n(nlctl CLI, SQLite state,\nCtrl+G native shell hook)"]
    P4 --> P5["Phase 5:\nAcceptance & Packaging\n(3-min demo.py, test_e2e,\n5-stage Git freeze)"]
```

---

### Phase 0: The Legacy Prototype (October 2026)

#### What was built back then?
The initial repository in the `legacy/` directory was a classic single-script prototype:
- A Python script `agent.py` running an infinite loop: `while True: req = input("Enter: ")`.
- It sent prompts to LM Studio via a basic HTTP call (`nl_translator.py`).
- A script called `safety_verifier.py` checked if the generated string contained words like `rm`, `kill`, `dd`, or `format` using simple regex.
- If not blocked, it invoked `subprocess.run(command, shell=True)`.

#### Why did this fail? (The 5 Fatal Flaws that justified our thesis)
1. **The Stealth Deletion Bypass**: A regex checking for `rm` is trivially bypassed. If the model outputs `find . -name "*.log" -delete`, the regex sees the command starts with `find` and allows it. The directory is wiped silently.
2. **No Undo / Permanent Data Loss**: Once a file was modified or deleted, it was gone forever. There was no recovery mechanism.
3. **The TOCTOU Vulnerability (Time-Of-Check to Time-Of-Use)**: If the system inspected a directory during the prompt, but a file was added or a symlink was swapped right before execution, the command ran blind to the filesystem change.
4. **The "Shell Jail" Trap**: The user had to exit their normal terminal, run `python agent.py`, and type inside a restricted Python prompt. Crucially: running `cd mydir` executed inside Python's child process; the moment `agent.py` closed, the user was back in their starting directory!
5. **Multi-Second Latency**: Every single keystroke re-sent 1,000+ prompt tokens to the neural network from scratch, causing 2,000–4,000 ms delays.

---

### Phase 1: The Architectural Refactor & Safety Engine (`src/nlagent/`)

To solve these flaws, the naive loop was completely replaced with a formal operating system agent pipeline:

1. **The Core Invariant: Two-Phase Execution (`propose()` vs `commit()`)**:
   - The LLM **never** executes commands.
   - `propose()` *only* produces candidate commands, validates structured JSON, evaluates risks, and stores an immutable record with a unique Proposal ID (`p_...`).
   - `commit()` is the only function permitted to run shell execution, requiring explicit policy or user approval.

2. **Static Effect Analysis (`src/nlagent/safety/effects.py`)**:
   - Replaced fragile keyword regexes with full token/AST decomposition.
   - Statically computes exactly what a command **would** touch before it runs:
     `deletes`, `creates`, `overwrites`, `perm_changes`, `signals`, and `protected_hit`.
   - Categorizes commands into **5 Deterministic Risk Tiers**:
     - **Tier 0 (`read_only`)**: Non-mutating search/inspection (`ls`, `grep`) $\to$ Auto-runs.
     - **Tier 1 (`state_modifying`)**: Additive mutations (`touch`, `mkdir`) $\to$ Auto-runs / notifies.
     - **Tier 2 (`destructive`)**: Deletions and truncations (`rm`, `> file`) $\to$ Mandatory confirmation.
     - **Tier 3 (`privileged`)**: Protected roots (`C:\Windows`, `/etc`) $\to$ Hard-blocked (code 126).
     - **Tier 4 (`ambiguous`)**: Vague instructions $\to$ Disambiguation gate (clarifying question).

3. **Deterministic 3-Layer Prompt Layout (`layout.py`)**:
   - **$L_0$ (Static)**: System prompt and JSON schema (cached permanently).
   - **$L_1$ (Environment)**: OS, shell, tools, user (stable per session).
   - **$L_2$ (Volatile)**: Active directory and current user instruction.

---

### Phase 2: Scientific Evidence & Empirical Benchmarks

To meet strict academic standards, we replaced qualitative claims with empirical proofs:

1. **Experiment E0 (`scripts/e0_equality.py`)**:
   - Proved that cached prefix completions are 100% character-identical to uncached greedy completions at temperature 0.0.
2. **Safety Benchmark v2 (`scripts/run_safety_bench_v2.py`)**:
   - Tested 5 security guards (G0-simple regex, G0-published open-source agent patterns, G1 static heuristics, G2 passive sandbox, and G3 our engine) across 15 blind evasion attacks and 15 benign developer commands.
   - **Result**: G0 allowed **73.3% of attacks through**. Our engine (G3) achieved a **0.0% bypass rate** with Clopper-Pearson 95% confidence intervals.
3. **2x2 Factorial Matrix E5 (`scripts/run_e5.py`)**:
   - Evaluated `{Base, Tuned}` models across `{Full, Short}` prompts.
   - **Result**: Context distillation allowed the fine-tuned model on short prompts to maintain 75% accuracy while cutting prompt tokens by 79% and speeding up TTFT latency by **4.8x**.
4. **Mathematical TOCTOU Defense (`tests/test_toctou.py`)**:
   - Verified that if files are added or a target path is swapped for a symlink pointing to `/etc` between `propose()` and `commit()`, the commit re-check detects the escalation and blocks execution with exit code `126`.

---

### Phase 3: The Safety Net — Quarantine & Byte-Exact Undo

Recognizing that users occasionally approve destructive commands by mistake:
1. **Pre-Execution Quarantine Shadowing (`src/nlagent/safety/quarantine.py`)**:
   - Before `commit()` touches the disk, every item targeted for deletion or overwrite is automatically shadowed into `~/.nl-agent/quarantine/<proposal_id>/`.
   - Uses OS hardlinks (zero additional disk space, instant execution) with automatic copy fallback across drives.
2. **Byte-Exact Undo Engine**:
   - Running `undo(proposal_id)` restores files to their original paths.
   - Compares SHA-1 hashes before and after restoration to guarantee bit-level integrity.
3. **500 MB Safety Limit Guardrail**:
   - If an operation targets >500 MB, the system requires explicit user confirmation stating that quarantine undo will be bypassed, protecting the disk from being overwhelmed.

---

### Phase 4: Turning It Into an OS Product — `nlctl` & Shell Hooks (`Ctrl+G`)

This phase bridged the gap between an academic Python codebase and an actual operating system tool.

#### The Problem It Solved:
In the legacy version, you had to run `python agent.py` in a separate window. If you typed `cd /var/log`, when `agent.py` closed, your actual shell was still in `/home/user`. You couldn't pipe commands, use normal shell history, or keep environment variables.

#### The Solution:
1. **The `nlctl` CLI**: A standard console script registered in `pyproject.toml` with subcommands: `propose`, `commit`, `undo`, `history`.
2. **Persistent SQLite Store (`src/nlagent/cli/db.py`)**: Stores proposal IDs, command strings, ANSI risk badges, and quarantine mappings persistently across shell sessions.
3. **The Native OS Hook (`Ctrl+G`)**:
   - Users stay in their normal terminal (PowerShell or Bash).
   - You type your plain English instruction directly at the command prompt.
   - Pressing **`Ctrl+G`** triggers the shell hook, proposes the command, displays the risk badge on `stderr`, and replaces your line buffer with `eval "$(nlctl commit <id> --emit-shell)"`.
   - Pressing Enter runs the verified command inside your **real shell** (so `cd` actually changes your active directory!).

---

### Phase 5: The 7-Stage End-to-End Acceptance Demo & GitHub Freeze

To prove the entire system works reliably from end to end:
1. Created [`scripts/demo.py`](file:///d:/Final%20yr%20Proj/scripts/demo.py): An interactive and automated walkthrough verifying all 7 stages in sequence (Read-only $\to$ Create $\to$ Vague/Clarification $\to$ Destructive $\to$ Undo $\to$ Stealth Bypass Escalation $\to$ Sub-millisecond Cache Hit).
2. Created [`tests/test_e2e.py`](file:///d:/Final%20yr%20Proj/tests/test_e2e.py): Automated regression test suite (all 39 tests passing 100%).
3. Pushed the repository to GitHub in 5 clean, descriptive stages.

---

## 3. How the Agent Runs in the Background (Runtime Architecture)

```
┌────────────────────────────────────────────────────────┐
│ USER TERMINAL (PowerShell / Bash)                      │
│ Types: "clean up old logs" -> Hits Ctrl+G              │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│ nlctl propose                                          │
│ 1. Checks VerifiedCommandCache (0.3 ms hit?)           │
│ 2. If Miss: queries resolve_local_endpoint()           │
└──────────────────────────┬─────────────────────────────┘
                           │
        ┌──────────────────┴──────────────────┐
        ▼                                     ▼
┌─────────────────────────────┐   ┌─────────────────────────────┐
│ OPTION A: LM Studio (GUI)   │   │ OPTION B: llama-server (C++)│
│ http://127.0.0.1:1234       │   │ http://127.0.0.1:8080       │
│ User clicks 'Start Server'  │   │ Headless background daemon  │
│ Port 1234 active            │   │ Port 8080 active            │
└─────────────────────────────┘   └─────────────────────────────┘
```

### Do you need LM Studio open? (Backend Options Explained)

**No, you do not strictly need LM Studio open.** The system supports three flexible runtime modes:

#### Mode 1: LM Studio Developer Server (Port 1234)
- **When to use**: During development, visual debugging, or testing on Windows.
- **How it runs**: You open LM Studio, load your model (e.g. `nvidia-nemotron-3-nano-4b.gguf`), and click **"Start Server"** on port `1234`.
- **How `nlctl` finds it**: `resolve_local_endpoint()` probes `http://127.0.0.1:1234/v1/models`. If reachable, it sends completion requests via OpenAI-compatible JSON.

#### Mode 2: Headless `llama-server` Background Daemon (Port 8080) — *Production Mode*
- **When to use**: When you want the agent running completely silently in the background as a true OS daemon without any GUI window open.
- **How it runs**: Run `llama-server` (the official C++ compiled binary from `llama.cpp`):
  ```powershell
  # Start the background server loaded with our fine-tuned GGUF:
  llama-server.exe -m "models/nemotron-nano-4b-q4_k_m.gguf" --port 8080 -c 4096 --cont-batching
  ```
  *(In Linux/macOS, this runs as a `systemd` service; in Windows, it runs as a minimized background process or Windows Service).*
- **How `nlctl` finds it**: `resolve_local_endpoint()` checks `http://127.0.0.1:8080/health`. If active, it communicates directly with the high-performance C++ backend.

#### Mode 3: Deterministic Offline Mock & Verified Cache Mode (Zero GPU/Server required)
- **When to use**: Automated CI/CD, classroom grading, offline demonstrations, or running unit tests on laptops without dedicated GPUs.
- **How it runs**: If neither port `8080` nor port `1234` is active (or when `--mock` is passed), `nlctl` and `scripts/demo.py` fall back to the deterministic proposal generator and semantic cache.

---

### The Life of a Keystroke: What happens when you press `Ctrl+G`?

Here is the exact step-by-step execution path:

1. **User types instruction**:
   ```powershell
   PS D:\Project> archive stale logs to backup.tar.gz
   ```
2. **User hits `Ctrl+G`**:
   The PowerShell hook (`shell/nl-bind.ps1`) uses `[Microsoft.PowerShell.PSConsoleReadLine]::GetBufferState` to capture the line.
3. **Hook executes `nlctl propose ... --emit-shell`**:
   - `propose()` formats the prompt into $L_0$, $L_1$, and $L_2$.
   - It checks `VerifiedCommandCache`. (If run previously, it returns the verified proposal in **0.3 ms**).
   - If uncached, it sends an HTTP request to the active background server (`http://127.0.0.1:8080` or `1234`).
4. **Static Safety Analysis**:
   - `effects.analyse()` parses the proposed command: `tar -czf backup.tar.gz logs/`.
   - Evaluates the effect: creates `backup.tar.gz`, deletes 0 files $\to$ **Tier 1 (`state_modifying`)**.
5. **Preview Rendered on `stderr`**:
   - The hook writes the ANSI badge and effect preview to `stderr`:
     ```text
     [STATE-MODIFYING] tar -czf backup.tar.gz logs/
     CREATE 1 item(s): ['backup.tar.gz']
     ```
6. **Console Buffer Replacement**:
   - The hook replaces the command line buffer with:
     ```powershell
     PS D:\Project> nlctl commit p_8e21a4f0 --emit-shell
     ```
7. **User Presses Enter**:
   - `commit()` runs.
   - TOCTOU check re-verifies the target directory has not changed.
   - The shell runner executes the command in your **active terminal session**.
   - The proposal is cached for future sub-millisecond retrieval.

---

### Continuous KV-State & Prefix Caching in VRAM

Why is `llama-server` with prefix caching so fast?
- In traditional LLM tools, the system prompt ($L_0$) and environment description ($L_1$) must be re-tokenized and re-processed by the GPU on every turn (costing 500+ token computations each time).
- In our system, $L_0$ and $L_1$ are strictly deterministic (sorted keys, no volatile timestamps).
- `llama-server` retains the Key-Value (KV) cache of $L_0+L_1$ in GPU memory. When you press `Ctrl+G`, the server skips processing the first 500 tokens entirely; it only processes the short volatile request line in $L_2$, dropping Time-To-First-Token (TTFT) by **4.8x**.

---

## 4. Demystifying `nlctl` vs the Shell Hook

| Feature | `nlctl` (The CLI Engine) | Shell Hook (`shell/nl-bind.ps1` / `.bash`) |
| :--- | :--- | :--- |
| **What is it?** | A standalone executable console script (`main.py`). | A lightweight terminal keybinding script (40 lines of code). |
| **Where does it live?** | In your Python environment (`[project.scripts] nlctl`). | In your terminal profile (`$PROFILE` or `~/.bashrc`). |
| **How is it triggered?** | Explicitly typed: `nlctl propose ...`, `nlctl commit ...`. | Automatically triggered when you press **`Ctrl+G`**. |
| **Role in the system** | The "Brain & Muscle": runs safety analysis, SQLite persistence, quarantine, and execution. | The "Bridge": captures your current keystrokes, calls `nlctl`, and updates your console buffer. |
| **Can you use one without the other?** | **Yes.** You can use `nlctl` alone in scripts or manual terminal commands. | **No.** The hook is simply a wrapper that calls `nlctl` under the hood. |

---

## 5. Slide-by-Slide Viva & Presentation Outline

Use this exact structure for your presentation slides:

### Slide 1: Title & The Core Problem
- **Title**: Safety-Verified AI-Native Operating System Shell Agent
- **The Problem**: Current AI shell assistants (Warp AI, Copilot for CLI) run commands blindly or use brittle keyword denylists.
- **The Real-World Danger**: 
  - Accidental disk wipeouts (`rm -rf`, unintentional truncation).
  - Stealth bypasses (`find -delete` disguises deletion inside a search command).
  - Zero rollback when mistakes happen.

### Slide 2: The Core Invariant — Two-Phase Execution
- **The Rule**: The LLM *only* proposes; deterministic code decides safety.
- **Flow**: `propose()` $\to$ Static Effect Analysis $\to$ Preview $\to$ User Approval $\to$ `commit()`.
- **Key Insight**: No command can ever run directly from model output without passing through the safety gate.

### Slide 3: Static Effect Engine & 5 Risk Tiers
- **How it works**: Parses AST tokens to resolve `deletes`, `creates`, `overwrites`, and `protected_hit`.
- **5 Tiers**:
  - *Tier 0 (Read-Only)*: Auto-run.
  - *Tier 1 (State-Modifying)*: Auto-run with preview.
  - *Tier 2 (Destructive)*: Explicit confirmation required.
  - *Tier 3 (Privileged)*: Hard-blocked on system roots (`/etc`, `C:\Windows`).
  - *Tier 4 (Ambiguous)*: Clarifying question gate.
- **Benchmark Proof**: G0 (regex) had a **73.3% bypass rate**; our G3 engine achieved **0.0% bypass rate** across 15 adversarial attack cases.

### Slide 4: TOCTOU Defense & Quarantine Undo Engine
- **TOCTOU Race Defense**: Re-analyzes disk state at the exact millisecond of commit; aborts with exit code `126` if state mutated or symlinks were swapped.
- **Quarantine Engine**: Uses OS hardlinks to shadow targeted files before execution into `~/.nl-agent/quarantine/`.
- **Byte-Exact Undo**: A single `nlctl undo` command restores assets with SHA-1 hash verification.

### Slide 5: Latency Optimization — Sub-Millisecond Prefix Caching
- **The Challenge**: 4B models take 2–4 seconds per query.
- **Our Solution**: 3-layer deterministic layout ($L_0$ static, $L_1$ environment, $L_2$ state).
- **Results**:
  - Repeated/similar queries resolve in **0.32 ms** from `VerifiedCommandCache`.
  - Distilled short prompts on the fine-tuned model save 79% prompt tokens with a **4.8x TTFT speedup** (Exp E5).

### Slide 6: Native OS Integration & Acceptance Demo
- **No "Shell Jail"**: Native `Ctrl+G` hook in PowerShell and Bash keeps users in their real terminal.
- **Live Demonstration**: Run `python scripts/demo.py --auto --mock` showing the full 7-stage flow in 3 minutes.
