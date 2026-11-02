# Fine-Tuning Dataset Audit & Generalisation Analysis

> **Audit Date**: 2026-11-02  
> **Train Dataset**: `datasets/finetune_train.jsonl` (7,200 rows)  
> **Val Dataset**: `datasets/finetune_val.jsonl` (800 rows)  
> **Near-Duplicate Threshold**: Word 3-gram Jaccard $\ge 0.9$  

## 1. Executive Summary & Critical Leakage Findings

> [!WARNING] **Data Leakage & Generalisation Alert**  
> An audit of the train/val split reveals that **77.42% of validation queries** (144 of 186) appear verbatim in the training set under different environment configurations.  
> Furthermore, **80.33% of (natural language request, target command) pairs** are identical across splits.  
> Consequently, standard validation loss and benchmark scores on this validation split primarily reflect **in-distribution memorisation and syntax adherence**, rather than true out-of-domain command synthesis.

### Key Split & Overlap Metrics

| Metric | Training Set | Validation Set | Cross-Split Overlap |
| :--- | :---: | :---: | :---: |
| **Total Instances** | 7,200 | 800 | 758 instances (94.75%) |
| **Distinct User Requests** | 545 | 186 | 144 queries (77.42%) |
| **Near-Duplicate Queries (Jaccard $\ge 0.9$)** | — | — | 144 queries (77.42%) |
| **Distinct Target Commands** | 736 | 233 | 186 commands (79.83%) |
| **(Request, Command) Pairs** | 742 | 239 | 192 pairs (80.33%) |
| **Template Families** | 6 | 6 | 6 shared (0 unseen) |

### Near-Duplicate Analysis (Jaccard $\ge 0.90$)

All near-duplicate queries with Jaccard $\ge 0.90$ correspond to exact normalised matches.

## 2. Template-Family Distribution & Overlap

| Template Family | Train Count | Train % | Val Count | Val % | Split Ratio |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `clarification` | 727 | 10.1% | 73 | 9.1% | 10.0:1 |
| `file_ops` | 2,391 | 33.2% | 268 | 33.5% | 8.9:1 |
| `git_vcs` | 1,038 | 14.4% | 127 | 15.9% | 8.2:1 |
| `networking` | 827 | 11.5% | 88 | 11.0% | 9.4:1 |
| `process_control` | 1,139 | 15.8% | 132 | 16.5% | 8.6:1 |
| `sys_info` | 1,078 | 15.0% | 112 | 14.0% | 9.6:1 |

## 3. Environment & Shell Distribution

| Shell Target | Train Count | Train % | Val Count | Val % | Balance Status |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `bash` | 2,459 | 34.2% | 251 | 31.4% | Balanced (33±2%) |
| `powershell` | 2,343 | 32.5% | 280 | 35.0% | Balanced (33±2%) |
| `zsh` | 2,398 | 33.3% | 269 | 33.6% | Balanced (33±2%) |

## 4. Safety & Risk Tier Distribution

Risk tier classification derived using the G1 baseline safety classifier (`src/nlagent/safety/baseline_g1.py`):

| Risk Tier | Train Count | Train % | Val Count | Val % | Description |
| :--- | :---: | :---: | :---: | :---: | :--- |
| **read_only** | 1,911 | 26.5% | 213 | 26.6% | Read-only inspection commands (`ls`, `ps`, `cat`, `df`) |
| **state_modifying** | 1,956 | 27.2% | 215 | 26.9% | Non-destructive state mutations (`mkdir`, `touch`, `cp`, `git checkout`) |
| **destructive** | 548 | 7.6% | 58 | 7.2% | Irreversible deletions / terminations (`rm`, `kill -9`, `git reset --hard`) |
| **ambiguous** | 2,058 | 28.6% | 241 | 30.1% | Pipelines, complex redirection, or unrecognised syntax |
| **clarification** | 727 | 10.1% | 73 | 9.1% | Agent asked clarifying question (no command executed) |

## 5. Clarification Request Distribution

| Flag State | Train Count | Train % | Val Count | Val % | Role |
| :--- | :---: | :---: | :---: | :---: | :--- |
| `needs_clarification = True` | 727 | 10.1% | 73 | 9.1% | Missing targets / ambiguous destructive action |
| `needs_clarification = False` | 6,473 | 89.9% | 727 | 90.9% | Direct terminal command generated |

## 6. Token Length Distribution Statistics

Token counts calculated via regex word/subword tokenization (`estimate_token_count`):

| Section | Split | Mean ± Std | Min | Median | p95 | Max |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **System** | Train | 74.0 ± 0.8 | 73 | 74 | 75 | 75 |
| | Val | 74.0 ± 0.8 | 73 | 74 | 75 | 75 |
| **User** | Train | 7.3 ± 3.4 | 3 | 7 | 11 | 24 |
| | Val | 7.3 ± 3.4 | 3 | 7 | 11 | 24 |
| **Assistant** | Train | 32.5 ± 8.0 | 22 | 29 | 49 | 57 |
| | Val | 32.5 ± 8.0 | 22 | 29 | 49 | 57 |
| **Total** | Train | 113.7 ± 10.0 | 99 | 112 | 132 | 155 |
| | Val | 113.7 ± 9.9 | 99 | 112 | 132 | 155 |

## 7. Target Commands & Root Utilities

- **Distinct Command Strings**: 736 (train), 233 (val), 186 shared (79.83%)
- **Distinct Root Utilities**: 36 (train), 36 (val)

### Top 10 Root Utilities in Training Set

| Rank | Utility | Frequency | Dominant OS / Ecosystem |
| :---: | :--- | :---: | :--- |
| 1 | `git` | 1,038 | POSIX / Cross-Platform |
| 2 | `whoami` | 279 | POSIX / Cross-Platform |
| 3 | `get-childitem` | 257 | PowerShell |
| 4 | `ls` | 204 | POSIX / Cross-Platform |
| 5 | `lsof` | 204 | POSIX / Cross-Platform |
| 6 | `cp` | 201 | POSIX / Cross-Platform |
| 7 | `ps` | 195 | POSIX / Cross-Platform |
| 8 | `curl` | 194 | POSIX / Cross-Platform |
| 9 | `stop-process` | 194 | PowerShell |
| 10 | `rm` | 191 | POSIX / Cross-Platform |

## 8. Recommendations for Benchmark Validity

1. **Implement Template-Disjoint Splitting**: Future iterations of `DestructiveBench` and validation sets should partition by template family or root utility, guaranteeing that the model encounters zero training requests during evaluation.
2. **De-duplicate Request Templates Across Envs**: While evaluating across bash, zsh, and powershell tests cross-shell translation, reporting raw validation loss overestimates test performance due to 77.4% query memorisation.
3. **Retain Destructive Balance**: The ~7.5% destructive proportion and ~10% clarification rate are well-calibrated for safety without causing trigger-happy refusal.

