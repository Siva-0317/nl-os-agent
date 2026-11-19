# Experiment E5: Live Nemotron-3-Nano Benchmark Evaluation (SEALED split)

## Executive Summary & Headline Results

Evaluates the live `Nemotron-3-Nano-4B` model across a rigorous paired factorial matrix on the **sealed** benchmark split.
Strictly tests whether instruction fine-tuning preserves execution accuracy, JSON validity, and clarification adherence
when system prompt context is reduced from full (~450 tokens) to short (<= 100 tokens), tested against baseline/zero-shot prompts.

- **Benchmark Split**: `sealed`
- **Evaluated Test Cases**: `50`
- **Backend Server**: `llama-server`
- **Model Under Test**: `base=NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf | tuned=NVIDIA-Nemotron-3-Nano-4B-Dense-Q4_K_M.gguf`

### Table 8.2: 2x2 Factorial Performance Matrix

| Model | Prompt Mode | Execution Accuracy (%) [95% Wilson CI] | JSON Validity (%) | Clarification Acc (%) | Mean Prompt Tokens | Mean TTFT (ms) | Mean Decode (ms) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Base** | `full` | **92.0%** [81.2%, 96.8%] | 100.0% | 100.0% | 484 t | 922.4 ms | 1250.6 ms |
| **Base** | `short` | **68.0%** [54.2%, 79.2%] | 100.0% | 0.0% | 119 t | 417.8 ms | 1197.6 ms |
| **Tuned** | `full` | **100.0%** [92.9%, 100.0%] | 100.0% | 100.0% | 484 t | 929.8 ms | 1125.6 ms |
| **Tuned** | `short` | **100.0%** [92.9%, 100.0%] | 100.0% | 100.0% | 118 t | 419.5 ms | 1145.7 ms |

### Statistical Significance (McNemar's Paired Tests)

Paired comparisons evaluate discordant outcomes and paired difference confidence intervals on identical benchmark queries:

| Comparison Pair | Condition A Pass / B Fail (b) | Condition A Fail / B Pass (c) | Paired Accuracy Diff (Δ) [95% CI] | Chi-Square (χ²) / Test Stat | p-value | Statistically Significant (p < 0.05) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `Tuned_Short_vs_Base_Short` | 16 | 0 | **+32.0%** [+18.9%, +45.1%] | 0.00 | 3.1000e-05 | **YES** (p < 0.05) |
| `Tuned_Short_vs_Base_Full` | 4 | 0 | **+8.0%** [+0.4%, +15.6%] | 0.00 | 0.1250 | No |
| `Tuned_Short_vs_Tuned_Full` | 0 | 0 | **+0.0%** [+0.0%, +0.0%] | 0.00 | 1.0000 | No |
| `Base_Full_vs_Base_Short` | 16 | 4 | **+24.0%** [+7.6%, +40.4%] | 0.00 | 0.0118 | **YES** (p < 0.05) |

### Efficiency & Latency Gains
- **Prompt Token Reduction**: **75.7%** (484 -> 118 tokens)
- **TTFT Prefill Speedup**: **2.22x** (929.8 ms -> 419.5 ms)
- **Accuracy Preservation**: **100.0%** (Tuned Short) vs **100.0%** (Tuned Full)

### Provenance & Environment
- **Run ID**: `a77e26874912`
- **Git SHA**: `0635e1c99b6b18b696ceff583fb35442f04386ee`
- **GPU / Hardware**: `NVIDIA GeForce RTX 2050`
- **Driver / Server**: `572.42` / `llama-server`
- **Model File**: `base=NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf | tuned=NVIDIA-Nemotron-3-Nano-4B-Dense-Q4_K_M.gguf`
- **Timestamp**: `2026-11-12T17:39:18.733767+00:00`
- **Seed**: `42`
