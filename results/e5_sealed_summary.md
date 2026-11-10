# Experiment E5: Live Nemotron-3-Nano Benchmark Evaluation (SEALED split)

## Executive Summary & Headline Results

Evaluates the live `Nemotron-3-Nano-4B` model across a rigorous paired factorial matrix on the **sealed** benchmark split.
Strictly tests whether instruction fine-tuning preserves execution accuracy, JSON validity, and clarification adherence
when system prompt context is reduced from full (~450 tokens) to short (<= 100 tokens), tested against baseline/zero-shot prompts.

- **Benchmark Split**: `sealed`
- **Evaluated Test Cases**: `50`
- **Backend Server**: `llama-server`
- **Model Under Test**: `NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf`

### Table 8.2: 2x2 Factorial Performance Matrix

| Model | Prompt Mode | Execution Accuracy (%) [95% Wilson CI] | JSON Validity (%) | Clarification Acc (%) | Mean Prompt Tokens | Mean TTFT (ms) | Mean Decode (ms) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Base** | `full` | **44.0%** [31.2%, 57.7%] | 100.0% | 22.2% | 420 t | 122.5 ms | 41.9 ms |
| **Base** | `short` | **32.0%** [20.8%, 45.8%] | 66.0% | 0.0% | 80 t | 24.4 ms | 42.0 ms |
| **Tuned** | `full` | **94.0%** [83.8%, 97.9%] | 100.0% | 100.0% | 420 t | 122.3 ms | 42.1 ms |
| **Tuned** | `short` | **94.0%** [83.8%, 97.9%] | 100.0% | 100.0% | 80 t | 24.6 ms | 42.0 ms |

### Statistical Significance (McNemar's Paired Tests)

Paired comparisons evaluate discordant outcomes and paired difference confidence intervals on identical benchmark queries:

| Comparison Pair | Condition A Pass / B Fail (b) | Condition A Fail / B Pass (c) | Paired Accuracy Diff (Δ) [95% CI] | Chi-Square (χ²) / Test Stat | p-value | Statistically Significant (p < 0.05) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `Tuned_Short_vs_Base_Short` | 31 | 0 | **+62.0%** [+48.4%, +75.6%] | 29.03 | 0.0000e+00 | **YES** (p < 0.05) |
| `Tuned_Short_vs_Base_Full` | 25 | 0 | **+50.0%** [+36.0%, +64.0%] | 23.04 | 2.0000e-06 | **YES** (p < 0.05) |
| `Tuned_Short_vs_Tuned_Full` | 0 | 0 | **+0.0%** [+0.0%, +0.0%] | 0.00 | 1.0000 | No |
| `Base_Full_vs_Base_Short` | 6 | 0 | **+12.0%** [+2.9%, +21.1%] | 0.00 | 0.0312 | **YES** (p < 0.05) |

### Efficiency & Latency Gains
- **Prompt Token Reduction**: **81.0%** (420 -> 80 tokens)
- **TTFT Prefill Speedup**: **4.96x** (122.3 ms -> 24.6 ms)
- **Accuracy Preservation**: **94.0%** (Tuned Short) vs **94.0%** (Tuned Full)

### Provenance & Environment
- **Run ID**: `bd21f3662393`
- **Git SHA**: `dfce40e50ae122bbf342a84ccbef0a6eaab4bfaf`
- **GPU / Hardware**: `NVIDIA GeForce RTX 2050`
- **Driver / Server**: `572.42` / `llama-server`
- **Model File**: `NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf`
- **Timestamp**: `2026-11-09T19:05:26.657752+00:00`
- **Seed**: `42`
