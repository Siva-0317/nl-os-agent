# Adaptive Adversarial Attacker Evaluation Report

## Executive Summary

Empirical evaluation of multi-stage evasion attacks against static denylists, heuristic AST parsers, and the combined static effect defense pipeline across PowerShell, cmd.exe, and Bash. All confidence intervals are exact **Clopper-Pearson 95% intervals**.

- **Engine Git SHA**: `dfce40e50ae122bbf342a84ccbef0a6eaab4bfaf`
- **Evaluated Attacks (Harmful)**: `544`
- **Evaluated Benign Controls**: `272`
- **Raw Evaluation Ledger**: [`results/adaptive_attacks.jsonl`](results/adaptive_attacks.jsonl)

### Primary Guard Robustness Matrix (Multi-Stage Attacks)

| Guard | Description | Bypass Rate (%) [95% Clopper-Pearson CI] | Interception Rate (%) | False Alarm Rate (%) [95% CI] | Confirmation Burden (prompts / 100 benign) |
| :--- | :--- | :---: | :---: | :---: | :---: |
| G0-simple | Minimal single-regex verb denylist | **71.5%** [67.5%, 75.3%] | 28.5% | 0.0% [0.0%, 1.4%] | 0.0 |
| G0-published | Literature multi-pattern denylist (AutoGPT/LangChain) | **66.4%** [62.2%, 70.3%] | 33.6% | 7.3% [4.5%, 11.1%] | 7.4 |
| G1 | Token & AST pipeline stage classifier | **20.6%** [17.3%, 24.2%] | 79.4% | 81.2% [76.1%, 85.7%] | 81.2 |
| G2 | Passive OS sandbox containment (no interception) | **100.0%** [99.3%, 100.0%] | 0.0% | 0.0% [0.0%, 1.4%] | 0.0 |
| G3 | Static effect engine (effects.analyse + tier_from_effect) | **4.2%** [2.7%, 6.3%] | 95.8% | 73.9% [68.2%, 79.0%] | 73.9 |
| **Combined G0-G3** | **Multi-tier defense in depth (G0 + G1 + G3)** | **1.5%** [0.6%, 2.9%] | 98.5% | 90.1% [85.9%, 93.4%] | 90.1 |

### Adversarial Bypass Breakdown by Evasion Stage Depth (Combined G0-G3)

| Stage Depth | Description | Attack Cases (n) | Bypasses | Bypass Rate (%) [95% CI] | Benign Controls (n) | False Positives |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: |
| **0** | Stage 0 (Raw seed commands) | 24 | 0 | **0.0%** [0.0%, 14.2%] | 12 | 9 |
| **1** | Stage 1 (Single atomic mutation) | 208 | 5 | **2.4%** [0.8%, 5.5%] | 104 | 88 |
| **2** | Stage 2 (Two-stage composition) | 144 | 3 | **2.1%** [0.4%, 6.0%] | 72 | 64 |
| **3** | Stage 3 (Three-stage composition) | 96 | 0 | **0.0%** [0.0%, 3.8%] | 48 | 48 |
| **4** | Stage 4 (Four-stage complex composition) | 72 | 0 | **0.0%** [0.0%, 5.0%] | 36 | 36 |

### Adversarial Robustness by Target Shell (Combined G0-G3)

| Target Shell | Harmful Attacks (n) | Bypasses | Bypass Rate (%) [95% CI] | Benign Commands (n) | False Positives | FP Rate (%) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `powershell` | 184 | 0 | **0.0%** [0.0%, 2.0%] | 92 | 90 | 97.8% |
| `cmd` | 176 | 0 | **0.0%** [0.0%, 2.1%] | 88 | 87 | 98.9% |
| `bash` | 184 | 8 | **4.3%** [1.9%, 8.4%] | 92 | 68 | 73.9% |

### Key Security Insights & Findings
1. **Defense in Depth**: While individual denylists (G0-simple: 71.5%, G0-published: 66.4%) suffer massive bypass rates under multi-stage quoting, carets, and wrappers, the **Combined G0-G3** pipeline achieves an interception rate of **98.5%** (Bypass Rate: **1.5%**).
2. **Opaque Wrapper Escalation**: When attackers nest multi-stage commands inside subshells (`cmd /c`, `powershell -c`, `eval`, or `base64 | sh`), the static effect engine G3 reliably flags them as `ambiguous` with `opaque=True`, escalating for user confirmation rather than permitting blind auto-execution.
3. **Stage-Depth Resilience**: Increasing evasion depth from Stage 1 to Stage 4 defeats simple regex matches, but triggers multiple structural red flags in AST effect analysis.
