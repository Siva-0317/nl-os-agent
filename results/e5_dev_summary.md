# Experiment E5: Live Nemotron-3-Nano Benchmark Evaluation (DEV split)

## Executive Summary & Headline Results

Evaluates the live `Nemotron-3-Nano-4B` model across a rigorous paired factorial matrix on the **dev** benchmark split.
Strictly tests whether instruction fine-tuning preserves execution accuracy, JSON validity, and clarification adherence
when system prompt context is reduced from full (~450 tokens) to short (<= 100 tokens), tested against baseline/zero-shot prompts.

- **Benchmark Split**: `dev`
- **Evaluated Test Cases**: `100`
- **Backend Server**: `llama-server`
- **Model Under Test**: `base=NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf | tuned=NVIDIA-Nemotron-3-Nano-4B-Dense-Q4_K_M.gguf`

### Table 8.2: 2x2 Factorial Performance Matrix

| Model | Prompt Mode | Execution Accuracy (%) [95% Wilson CI] | JSON Validity (%) | Clarification Acc (%) | Mean Prompt Tokens | Mean TTFT (ms) | Mean Decode (ms) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Base** | `full` | **77.0%** [67.8%, 84.2%] | 100.0% | 66.7% | 304 t | 1908.0 ms | 2528.8 ms |
| **Base** | `short` | **56.0%** [46.2%, 65.3%] | 100.0% | 0.0% | 77 t | 967.5 ms | 2321.4 ms |
| **Tuned** | `full` | **85.0%** [76.7%, 90.7%] | 100.0% | 73.3% | 304 t | 1889.3 ms | 2214.8 ms |
| **Tuned** | `short` | **85.0%** [76.7%, 90.7%] | 99.0% | 93.3% | 78 t | 949.6 ms | 2149.6 ms |

### Statistical Significance (McNemar's Paired Tests)

Paired comparisons evaluate discordant outcomes and paired difference confidence intervals on identical benchmark queries:

| Comparison Pair | Condition A Pass / B Fail (b) | Condition A Fail / B Pass (c) | Paired Accuracy Diff (Δ) [95% CI] | Chi-Square (χ²) / Test Stat | p-value | Statistically Significant (p < 0.05) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `Tuned_Short_vs_Base_Short` | 32 | 3 | **+29.0%** [+18.8%, +39.2%] | 22.40 | 2.0000e-06 | **YES** (p < 0.05) |
| `Tuned_Short_vs_Base_Full` | 13 | 5 | **+8.0%** [-0.2%, +16.2%] | 0.00 | 0.0963 | No |
| `Tuned_Short_vs_Tuned_Full` | 8 | 8 | **+0.0%** [-7.9%, +7.9%] | 0.00 | 1.0000 | No |
| `Base_Full_vs_Base_Short` | 25 | 4 | **+21.0%** [+11.2%, +30.8%] | 13.79 | 2.0400e-04 | **YES** (p < 0.05) |

### Efficiency & Latency Gains
- **Prompt Token Reduction**: **74.5%** (304 -> 78 tokens)
- **TTFT Prefill Speedup**: **1.99x** (1889.3 ms -> 949.6 ms)
- **Accuracy Preservation**: **85.0%** (Tuned Short) vs **85.0%** (Tuned Full)

### Provenance & Environment
- **Run ID**: `3dc55297dd6e`
- **Git SHA**: `0635e1c99b6b18b696ceff583fb35442f04386ee`
- **GPU / Hardware**: `NVIDIA GeForce RTX 2050`
- **Driver / Server**: `572.42` / `llama-server`
- **Model File**: `base=NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf | tuned=NVIDIA-Nemotron-3-Nano-4B-Dense-Q4_K_M.gguf`
- **Timestamp**: `2026-11-12T16:54:26.917230+00:00`
- **Seed**: `42`
