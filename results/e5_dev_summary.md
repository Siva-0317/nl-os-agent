# Experiment E5: Live Nemotron-3-Nano Benchmark Evaluation (DEV split)

## Executive Summary & Headline Results

Evaluates the live `Nemotron-3-Nano-4B` model across a rigorous paired factorial matrix on the **dev** benchmark split.
Strictly tests whether instruction fine-tuning preserves execution accuracy, JSON validity, and clarification adherence
when system prompt context is reduced from full (~450 tokens) to short (<= 100 tokens), tested against baseline/zero-shot prompts.

- **Benchmark Split**: `dev`
- **Evaluated Test Cases**: `100`
- **Backend Server**: `llama-server`
- **Model Under Test**: `NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf`

### Table 8.2: 2x2 Factorial Performance Matrix

| Model | Prompt Mode | Execution Accuracy (%) [95% Wilson CI] | JSON Validity (%) | Clarification Acc (%) | Mean Prompt Tokens | Mean TTFT (ms) | Mean Decode (ms) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Base** | `full` | **52.0%** [42.3%, 61.5%] | 100.0% | 6.7% | 825 t | 122.4 ms | 42.0 ms |
| **Base** | `short` | **31.0%** [22.8%, 40.6%] | 67.0% | 0.0% | 87 t | 24.5 ms | 42.0 ms |
| **Tuned** | `full` | **65.0%** [55.2%, 73.6%] | 100.0% | 6.7% | 825 t | 122.4 ms | 42.0 ms |
| **Tuned** | `short` | **67.0%** [57.3%, 75.4%] | 100.0% | 6.7% | 87 t | 24.5 ms | 42.0 ms |

### Statistical Significance (McNemar's Paired Tests)

Paired comparisons evaluate discordant outcomes and paired difference confidence intervals on identical benchmark queries:

| Comparison Pair | Condition A Pass / B Fail (b) | Condition A Fail / B Pass (c) | Paired Accuracy Diff (Δ) [95% CI] | Chi-Square (χ²) / Test Stat | p-value | Statistically Significant (p < 0.05) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `Tuned_Short_vs_Base_Short` | 36 | 0 | **+36.0%** [+26.5%, +45.5%] | 34.03 | 0.0000e+00 | **YES** (p < 0.05) |
| `Tuned_Short_vs_Base_Full` | 17 | 2 | **+15.0%** [+6.9%, +23.1%] | 0.00 | 7.2900e-04 | **YES** (p < 0.05) |
| `Tuned_Short_vs_Tuned_Full` | 2 | 0 | **+2.0%** [-0.8%, +4.8%] | 0.00 | 0.5000 | No |
| `Base_Full_vs_Base_Short` | 21 | 0 | **+21.0%** [+13.0%, +29.0%] | 0.00 | 1.0000e-06 | **YES** (p < 0.05) |

### Efficiency & Latency Gains
- **Prompt Token Reduction**: **89.4%** (825 -> 87 tokens)
- **TTFT Prefill Speedup**: **5.00x** (122.4 ms -> 24.5 ms)
- **Accuracy Preservation**: **67.0%** (Tuned Short) vs **65.0%** (Tuned Full)

### Provenance & Environment
- **Run ID**: `daf7ffd064bb`
- **Git SHA**: `dfce40e50ae122bbf342a84ccbef0a6eaab4bfaf`
- **GPU / Hardware**: `NVIDIA GeForce RTX 2050`
- **Driver / Server**: `572.42` / `llama-server`
- **Model File**: `NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf`
- **Timestamp**: `2026-11-09T18:40:02.268033+00:00`
- **Seed**: `42`
