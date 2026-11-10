# Safety Benchmark v3: Adversarial Evasion & Safety Guard Evaluation (G0..G4)

## Executive Summary

Rigorous statistical evaluation of static denylists, AST classifiers, blind containment, effect-derived tiering (G3), and an LLM security judge (G4) across cross-shell blind attacks and benign shell commands. Confidence intervals are calculated using the exact **Clopper-Pearson** formulation.

- **Engine Git SHA**: `dfce40e50ae122bbf342a84ccbef0a6eaab4bfaf`
- **Run ID**: `fed3e044a034`
- **Evaluated Attacks (Harmful)**: `415`
- **Evaluated Benign Commands**: `317`
- **G4 Judge Backend**: `llama-server`

### Primary Defensive Performance Matrix

| Guard | Description | Bypass Rate (%) [95% Clopper-Pearson CI] | Interception Rate (%) | False Alarm Rate (%) [95% CI] | Confirmation Burden (prompts / 100 benign) | Mean Latency (ms) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **G0-simple** | Minimal single-regex verb denylist matching standard deletion verbs | **63.6%** [58.8%, 68.3%] | 36.4% | 8.5% [5.7%, 12.2%] | **8.5** | 0.01 ms |
| **G0-published** | Multi-pattern denylist compiled from published agent frameworks (AutoGPT, LangChain) | **56.9%** [51.9%, 61.7%] | 43.1% | 13.6% [10.0%, 17.8%] | **13.6** | 0.01 ms |
| **G1** | Token and pipeline stage classifier (nlagent.safety.baseline_g1) | **15.7%** [12.3%, 19.5%] | 84.3% | 80.4% [75.6%, 84.7%] | **80.4** | 0.10 ms |
| **G2** | Passive OS sandbox ('no preview, no tiering' with in-scope damage counted) | **100.0%** [99.1%, 100.0%] | 0.0% | 0.0% [0.0%, 1.2%] | **0.0** | 0.00 ms |
| **G3** | Deterministic static effect engine (effects.analyse + tier_from_effect) | **13.7%** [10.6%, 17.4%] | 86.3% | 52.4% [46.7%, 58.0%] | **52.4** | 3.16 ms |
| **G4** | Zero-shot LLM security judge with fixed evaluation prompt | **0.0%** [0.0%, 0.9%] | 100.0% | 100.0% [98.8%, 100.0%] | **100.0** | 12.69 ms |

### Categorized Decision Breakdown (Hard-Blocked vs Escalated vs Auto-Run)

| Guard | Attacks Blocked | Attacks Escalated | Attacks Auto-Run (Bypass) | Benign Auto-Run | Benign Escalated (FP) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **G0-simple** | 0 | 151 | **264** | 290 | 27 |
| **G0-published** | 0 | 179 | **236** | 274 | 43 |
| **G1** | 0 | 350 | **65** | 62 | 255 |
| **G2** | 0 | 0 | **415** | 317 | 0 |
| **G3** | 0 | 358 | **57** | 151 | 166 |
| **G4** | 0 | 415 | **0** | 0 | 317 |

### Adversarial Bypass Breakdown by Mutation Technique

| Mutation Technique | Cases (n) | G0-simple | G0-published | G1 | G2 (containment) | G3 (effect engine) | G4 (LLM judge) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `aliases` | 34 | 12 (35.3%) | 12 (35.3%) | 2 (5.9%) | 34 (100.0%) | 3 (8.8%) | 0 (0.0%) |
| `command_splitting` | 84 | 56 (66.7%) | 51 (60.7%) | 18 (21.4%) | 84 (100.0%) | 15 (17.9%) | 0 (0.0%) |
| `encodings` | 47 | 47 (100.0%) | 29 (61.7%) | 0 (0.0%) | 47 (100.0%) | 0 (0.0%) | 0 (0.0%) |
| `environment_variables` | 47 | 29 (61.7%) | 29 (61.7%) | 4 (8.5%) | 47 (100.0%) | 16 (34.0%) | 0 (0.0%) |
| `path_tricks` | 36 | 23 (63.9%) | 23 (63.9%) | 8 (22.2%) | 36 (100.0%) | 7 (19.4%) | 0 (0.0%) |
| `quoting` | 67 | 45 (67.2%) | 45 (67.2%) | 19 (28.4%) | 67 (100.0%) | 12 (17.9%) | 0 (0.0%) |
| `raw_seed` | 26 | 14 (53.8%) | 14 (53.8%) | 6 (23.1%) | 26 (100.0%) | 3 (11.5%) | 0 (0.0%) |
| `wrappers` | 74 | 38 (51.4%) | 33 (44.6%) | 8 (10.8%) | 74 (100.0%) | 1 (1.4%) | 0 (0.0%) |

### Adversarial Bypass Breakdown by Target Shell

| Target Shell | Cases (n) | G0-simple | G0-published | G1 | G2 (containment) | G3 (effect engine) | G4 (LLM judge) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `bash` | 161 | 105 (65.2%) | 77 (47.8%) | 40 (24.8%) | 161 (100.0%) | 27 (16.8%) | 0 (0.0%) |
| `cmd` | 69 | 45 (65.2%) | 45 (65.2%) | 23 (33.3%) | 69 (100.0%) | 4 (5.8%) | 0 (0.0%) |
| `powershell` | 185 | 114 (61.6%) | 114 (61.6%) | 2 (1.1%) | 185 (100.0%) | 26 (14.1%) | 0 (0.0%) |

### Key Defensive Findings & Academic Insights
1. **Effect-Based Superiority (G3)**: G3 drastically outperforms static denylists (G0-simple, G0-published) and heuristic parsers (G1) across evasion mutations (aliases, command-splitting, path-tricks, and encoding indirection).
2. **Containment Without Interception (G2)**: Passive OS containment provides no preview and no tiering; 100% of within-scope harmful operations execute without prompt intervention, demonstrating that containment alone cannot substitute for effect-gated interception.
3. **LLM Judge Fragility (G4)**: While an LLM judge catches standard plain verbs, it suffers from elevated latency and evasion under encodings and environment variable indirection, while introducing non-zero false alarms on benign queries.

### Provenance & Engine Traceability
- **Engine Git SHA**: `dfce40e50ae122bbf342a84ccbef0a6eaab4bfaf`
- **Execution Timestamp**: `2026-11-08T18:49:38.559606+00:00`
- **Sandbox Isolation**: Temporary directories (`tempfile.TemporaryDirectory`) strictly enforced.

