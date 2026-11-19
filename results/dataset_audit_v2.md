# Fine-Tuning Dataset Audit & Generalisation Analysis

> **Audit Date**: 2026-11-02  
> **Train Dataset**: `datasets/finetune_v2_train.jsonl` (10,800 rows)  
> **Val Dataset**: `datasets/finetune_v2_val.jsonl` (1,200 rows)  
> **Near-Duplicate Threshold**: Word 3-gram Jaccard $\ge 0.9$  

## 1. Executive Summary & Critical Leakage Findings

> [!WARNING] **Data Leakage & Generalisation Alert**  
> An audit of the train/val split reveals that **1.26% of validation queries** (6 of 478) appear verbatim in the training set under different environment configurations.  
> Furthermore, **1.07% of (natural language request, target command) pairs** are identical across splits.  
> Consequently, standard validation loss and benchmark scores on this validation split primarily reflect **in-distribution memorisation and syntax adherence**, rather than true out-of-domain command synthesis.

### Key Split & Overlap Metrics

| Metric | Training Set | Validation Set | Cross-Split Overlap |
| :--- | :---: | :---: | :---: |
| **Total Instances** | 10,800 | 1,200 | 48 instances (4.0%) |
| **Distinct User Requests** | 2,882 | 478 | 6 queries (1.26%) |
| **Near-Duplicate Queries (Jaccard $\ge 0.9$)** | — | — | 6 queries (1.26%) |
| **Distinct Target Commands** | 1,524 | 462 | 28 commands (6.06%) |
| **(Request, Command) Pairs** | 3,927 | 653 | 7 pairs (1.07%) |
| **Template Families** | 5 | 5 | 5 shared (0 unseen) |

### Near-Duplicate Analysis (Jaccard $\ge 0.90$)

All near-duplicate queries with Jaccard $\ge 0.90$ correspond to exact normalised matches.

## 2. Template-Family Distribution & Overlap

| Template Family | Train Count | Train % | Val Count | Val % | Split Ratio |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `clarification` | 972 | 9.0% | 108 | 9.0% | 9.0:1 |
| `file_ops` | 4,173 | 38.6% | 472 | 39.3% | 8.8:1 |
| `git_vcs` | 678 | 6.3% | 75 | 6.2% | 9.0:1 |
| `process_mgmt` | 1,726 | 16.0% | 173 | 14.4% | 10.0:1 |
| `read_only` | 3,251 | 30.1% | 372 | 31.0% | 8.7:1 |

## 3. Environment & Shell Distribution

| Shell Target | Train Count | Train % | Val Count | Val % | Balance Status |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `bash` | 3,511 | 32.5% | 375 | 31.2% | Balanced (33±2%) |
| `cmd` | 2,048 | 19.0% | 216 | 18.0% | Skewed |
| `powershell` | 4,388 | 40.6% | 502 | 41.8% | Skewed |
| `zsh` | 853 | 7.9% | 107 | 8.9% | Skewed |

## 4. Safety & Risk Tier Distribution

Risk tier classification derived using the G1 baseline safety classifier (`src/nlagent/safety/baseline_g1.py`):

| Risk Tier | Train Count | Train % | Val Count | Val % | Description |
| :--- | :---: | :---: | :---: | :---: | :--- |
| **read_only** | 1,568 | 14.5% | 183 | 15.2% | Read-only inspection commands (`ls`, `ps`, `cat`, `df`) |
| **state_modifying** | 2,657 | 24.6% | 298 | 24.8% | Non-destructive state mutations (`mkdir`, `touch`, `cp`, `git checkout`) |
| **destructive** | 668 | 6.2% | 64 | 5.3% | Irreversible deletions / terminations (`rm`, `kill -9`, `git reset --hard`) |
| **ambiguous** | 4,935 | 45.7% | 547 | 45.6% | Pipelines, complex redirection, or unrecognised syntax |
| **clarification** | 972 | 9.0% | 108 | 9.0% | Agent asked clarifying question (no command executed) |

## 5. Clarification Request Distribution

| Flag State | Train Count | Train % | Val Count | Val % | Role |
| :--- | :---: | :---: | :---: | :---: | :--- |
| `needs_clarification = True` | 972 | 9.0% | 108 | 9.0% | Missing targets / ambiguous destructive action |
| `needs_clarification = False` | 9,828 | 91.0% | 1,092 | 91.0% | Direct terminal command generated |

## 6. Token Length Distribution Statistics

Token counts calculated via regex word/subword tokenization (`estimate_token_count`):

| Section | Split | Mean ± Std | Min | Median | p95 | Max |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **System** | Train | 253.2 ± 194.2 | 93 | 96 | 671 | 672 |
| | Val | 245.2 ± 193.5 | 93 | 96 | 671 | 672 |
| **User** | Train | 15.5 ± 13.2 | 3 | 9 | 41 | 59 |
| | Val | 15.4 ± 13.5 | 4 | 8 | 41 | 60 |
| **Assistant** | Train | 33.9 ± 6.8 | 22 | 34 | 45 | 55 |
| | Val | 34.2 ± 6.6 | 22 | 34 | 45 | 55 |
| **Total** | Train | 302.6 ± 196.8 | 119 | 165 | 714 | 773 |
| | Val | 294.8 ± 196.7 | 119 | 161 | 716 | 774 |

## 7. Target Commands & Root Utilities

- **Distinct Command Strings**: 1,524 (train), 462 (val), 28 shared (6.06%)
- **Distinct Root Utilities**: 61 (train), 61 (val)

### Top 10 Root Utilities in Training Set

| Rank | Utility | Frequency | Dominant OS / Ecosystem |
| :---: | :--- | :---: | :--- |
| 1 | `git` | 896 | POSIX / Cross-Platform |
| 2 | `get-childitem` | 763 | PowerShell |
| 3 | `remove-item` | 432 | PowerShell |
| 4 | `get-content` | 394 | PowerShell |
| 5 | `rm` | 388 | POSIX / Cross-Platform |
| 6 | `mkdir` | 378 | POSIX / Cross-Platform |
| 7 | `ls` | 371 | POSIX / Cross-Platform |
| 8 | `new-item` | 369 | POSIX / Cross-Platform |
| 9 | `find` | 300 | POSIX / Cross-Platform |
| 10 | `dir` | 290 | POSIX / Cross-Platform |

## 8. Recommendations for Benchmark Validity

1. **Implement Template-Disjoint Splitting**: Future iterations of `DestructiveBench` and validation sets should partition by template family or root utility, guaranteeing that the model encounters zero training requests during evaluation.
2. **De-duplicate Request Templates Across Envs**: While evaluating across bash, zsh, and powershell tests cross-shell translation, reporting raw validation loss overestimates test performance due to 77.4% query memorisation.
3. **Retain Destructive Balance**: The ~7.5% destructive proportion and ~10% clarification rate are well-calibrated for safety without causing trigger-happy refusal.

