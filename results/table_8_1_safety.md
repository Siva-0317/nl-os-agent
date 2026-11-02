### Table 8.1: Adversarial Bypass & Precision Evaluation (DestructiveBench-mini)

| Guard | Defensive Architecture | Bypass Rate (%) | Interception Rate (%) | False Positive Rate (%) | Mean Latency (ms) | p50 Latency (ms) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **G0** | Static Denylist (Regex verbs) | **49.0%** (24/49) | **51.0%** | **20.8%** (5/24) | 0.02 ms | 0.02 ms |
| **G1** | Name & Pipeline Parser (Baseline G1) | **30.6%** (15/49) | **69.4%** | **25.0%** (6/24) | 0.24 ms | 0.24 ms |
| **G2** | Containment Only (Blind sandbox) | **100.0%** (49/49) | **0.0%** | **0.0%** (0/24) | 0.00 ms | 0.00 ms |
| **G3** | Proposed Effect Engine (G3) | **0.0%** (0/49) | **100.0%** | **8.3%** (2/24) | 2.91 ms | 0.41 ms |

#### Bypass Breakdown by Evasion Technique

| Evasion Technique / Family | Cases | G0 Bypasses | G1 Bypasses | G2 Bypasses | G3 Bypasses |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `alternate_tool` | 6 | 5 | 2 | 6 | **0** |
| `benign_look_alikes` | 10 | 0 | 0 | 0 | **0** |
| `benign_read_only` | 8 | 0 | 0 | 0 | **0** |
| `benign_state_modifying` | 6 | 0 | 0 | 0 | **0** |
| `chained_piped` | 4 | 0 | 0 | 4 | **0** |
| `destructive` | 1 | 1 | 1 | 1 | **0** |
| `direct` | 8 | 0 | 0 | 8 | **0** |
| `indirection` | 6 | 3 | 0 | 6 | **0** |
| `interpreter_one_liners` | 5 | 3 | 0 | 5 | **0** |
| `move_over` | 4 | 4 | 4 | 4 | **0** |
| `permission_changes` | 2 | 2 | 2 | 2 | **0** |
| `privileged_paths` | 5 | 2 | 2 | 5 | **0** |
| `process_kills` | 3 | 0 | 0 | 3 | **0** |
| `truncation_redirect` | 5 | 4 | 4 | 5 | **0** |
