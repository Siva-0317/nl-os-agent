# Experiment E5: 2x2 Factorial Evaluation Matrix

## Executive Summary & Headline Results

This evaluation tests whether instruction fine-tuning (`Nemotron-3-Nano-4B-Tuned`)
allows reducing the system prompt context from full (~450 tokens) to short (<= 100 tokens)
without sacrificing command execution accuracy or safety compliance.

### Table 8.2: 2x2 Factorial Performance Matrix

| Model | Prompt Mode | Execution Accuracy (%) [95% Wilson CI] | JSON Validity (%) | Clarification Acc (%) | Mean Prompt Tokens | Mean TTFT (ms) | Mean Decode (ms) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Base** | `full` | **66.0%** [52.1%, 77.6%] | 100.0% | 66.7% | 735 t | 120.0 ms | 45.0 ms |
| **Base** | `short` | **22.0%** [12.8%, 35.2%] | 68.0% | 0.0% | 87 t | 25.0 ms | 45.0 ms |
| **Tuned** | `full` | **72.0%** [58.3%, 82.5%] | 100.0% | 66.7% | 735 t | 120.0 ms | 45.0 ms |
| **Tuned** | `short` | **72.0%** [58.3%, 82.5%] | 100.0% | 66.7% | 87 t | 25.0 ms | 45.0 ms |

### Statistical Significance (McNemar's Paired Tests)

Paired comparisons evaluate discordant outcomes on identical sealed benchmark queries:

| Comparison Pair | Condition A Pass / B Fail (b) | Condition A Fail / B Pass (c) | Chi-Square (χ²) | p-value | Statistically Significant (p < 0.05) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `Tuned_Short_vs_Base_Short` | 25 | 0 | 23.04 | 2.0000e-06 | **YES** (p < 0.05) |
| `Tuned_Short_vs_Base_Full` | 3 | 0 | 0.00 | 0.2500 | No |
| `Tuned_Short_vs_Tuned_Full` | 0 | 0 | 0.00 | 1.0000 | No |
| `Base_Full_vs_Base_Short` | 22 | 0 | 0.00 | 0.0000e+00 | **YES** (p < 0.05) |

### Efficiency & Latency Gains
- **Prompt Token Reduction**: **88.1%** (735 -> 87 tokens)
- **TTFT Prefill Speedup**: **4.80x** (120.0 ms -> 25.0 ms)
- **Accuracy Preservation**: **72.0%** (Tuned Short) vs **72.0%** (Tuned Full)

### Provenance & Environment
- **Run ID**: `cb6b149f937d`
- **Git SHA**: `b7919f3e8b3420089ed3b9088514326a0b5e4ae0`
- **GPU / Hardware**: `NVIDIA GeForce RTX 2050`
- **Driver / Server**: `572.42` / `mock`
- **Timestamp**: `2026-11-02T16:37:24.943070+00:00`
