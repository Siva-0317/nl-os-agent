# Scientific Evidence Ledger & Data Authenticity Audit

> **Purpose**: This ledger audits every benchmark record in `results/*.jsonl` and input set in `datasets/`.
> It enforces that all reported numbers derive from live, authenticated hardware runs with full provenance.
> Synthetic mock backends and simulated timings are flagged `INVALID` and blocked from final figures.

---

## 1. Executive Summary Table

| File | Type | Rows | Backends | Model File (SHA256) | Conditions (n) | Provenance | Flags | Status |
| :--- | :---: | :---: | :--- | :--- | :--- | :---: | :--- | :---: |
| `results/ablation.jsonl` | result | 3795 | llama-server | NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf | 15 conditions (total 3795) | ✅ Yes | *(clean)* | **`VALID`** |
| `results/adaptive_attacks.jsonl` | result | 816 | guard-pipeline-g0-g3 | NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf | 10 conditions (total 816) | ✅ Yes | *(clean)* | **`VALID`** |
| `results/context_eval.jsonl` | result | 120 | *(none)* | *(none)* |  | ❌ Missing | MISSING_PROVENANCE | ❌ **`INVALID`** |
| `results/e0.jsonl` | result | 50 | *(none)* | *(none)* | read_only: 21, state_modifying: 16, destructive: 13 | ❌ Missing | MISSING_PROVENANCE | ❌ **`INVALID`** |
| `results/e0_real.jsonl` | result | 50 | lm-studio | NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf | read_only: 21, state_modifying: 16, destructive: 13 | ✅ Yes | *(clean)* | **`VALID`** |
| `results/e5_dev.jsonl` | result | 400 | llama-server | NVIDIA-Nemotron-3-Nano-4B-Dense-Q4_K_M.gguf, NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf (`22cb89cb`) | 4 conditions (total 400) | ✅ Yes | *(clean)* | **`VALID`** |
| `results/e5_matrix.jsonl` | result | 200 | llama-server | NVIDIA-Nemotron-3-Nano-4B-Dense-Q4_K_M.gguf, NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf (`22cb89cb`) | 4 conditions (total 200) | ✅ Yes | *(clean)* | **`VALID`** |
| `results/e5_sealed.jsonl` | result | 200 | llama-server | NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf | 4 conditions (total 200) | ✅ Yes | *(clean)* | **`VALID`** |
| `results/hardware_matrix.jsonl` | result | 3 | llama-server-cpu, llama-server-cuda, llama-server-vulkan | NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf |  | ✅ Yes | *(clean)* | **`VALID`** |
| `results/latency_breakdown.jsonl` | result | 200 | mock | NVIDIA-Nemotron-3-Nano-4B-Dense-Q4_K_M.gguf | reasoning_on: 100, reasoning_off: 100 | ✅ Yes | MOCK_OR_DEMO_BACKEND | ❌ **`INVALID`** |
| `results/learned_escalation.jsonl` | result | 16 | learned-escalation-g5 | NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf |  | ✅ Yes | *(clean)* | **`VALID`** |
| `results/safety.jsonl` | result | 73 | static_analysis | effects_engine_v1 | 14 conditions (total 73) | ✅ Yes | *(clean)* | **`VALID`** |
| `results/safety_v2.jsonl` | result | 1200 | *(none)* | *(none)* | 5 conditions (total 1200) | ❌ Missing | MISSING_PROVENANCE | ❌ **`INVALID`** |
| `results/safety_v3.jsonl` | result | 4392 | llama-server | NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf | 6 conditions (total 4392) | ✅ Yes | *(clean)* | **`VALID`** |
| `results/sealed_audit.jsonl` | audit | 1 | *(none)* | *(none)* |  | ✅ Yes | *(clean)* | **`VALID`** |
| `results/sealed_final.jsonl` | result | 300 | guard-pipeline-g0-g3, llama-server | NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf | 14 conditions (total 300) | ✅ Yes | *(clean)* | **`VALID`** |
| `datasets/attacks_v3_bash.jsonl` | dataset | 250 | *(none)* | *(none)* | 9 conditions (total 250) | N/A (Dataset) | *(clean)* | **`VALID`** |
| `datasets/attacks_v3_cmd.jsonl` | dataset | 199 | *(none)* | *(none)* | 9 conditions (total 199) | N/A (Dataset) | *(clean)* | **`VALID`** |
| `datasets/attacks_v3_powershell.jsonl` | dataset | 283 | *(none)* | *(none)* | 9 conditions (total 283) | N/A (Dataset) | *(clean)* | **`VALID`** |
| `datasets/benign_commands.jsonl` | dataset | 120 | *(none)* | *(none)* | benign_read_only: 66, benign_look_alike: 30, benign_state_modifying: 24 | N/A (Dataset) | *(clean)* | **`VALID`** |
| `datasets/benign_v3.jsonl` | dataset | 317 | *(none)* | *(none)* | 10 conditions (total 317) | N/A (Dataset) | *(clean)* | **`VALID`** |
| `datasets/context_eval_cases.jsonl` | dataset | 20 | *(none)* | *(none)* |  | N/A (Dataset) | *(clean)* | **`VALID`** |
| `datasets/dev_v3.jsonl` | dataset | 100 | *(none)* | *(none)* | 4 conditions (total 100) | N/A (Dataset) | *(clean)* | **`VALID`** |
| `datasets/e5_sealed.jsonl` | dataset | 50 | *(none)* | *(none)* | file_ops: 29, read_only: 12, clarification: 9 | N/A (Dataset) | *(clean)* | **`VALID`** |
| `datasets/finetune_train.jsonl` | dataset | 7200 | *(none)* | *(none)* | 6 conditions (total 7200) | N/A (Dataset) | *(clean)* | **`VALID`** |
| `datasets/finetune_v2_train.jsonl` | dataset | 10800 | *(none)* | *(none)* | 5 conditions (total 10800) | N/A (Dataset) | *(clean)* | **`VALID`** |
| `datasets/finetune_v2_val.jsonl` | dataset | 1200 | *(none)* | *(none)* | 5 conditions (total 1200) | N/A (Dataset) | *(clean)* | **`VALID`** |
| `datasets/finetune_val.jsonl` | dataset | 800 | *(none)* | *(none)* | 6 conditions (total 800) | N/A (Dataset) | *(clean)* | **`VALID`** |
| `datasets/safety_bench.jsonl` | dataset | 73 | *(none)* | *(none)* | 14 conditions (total 73) | N/A (Dataset) | *(clean)* | **`VALID`** |
| `datasets/safety_bench_v2_attacks.jsonl` | dataset | 120 | *(none)* | *(none)* | 10 conditions (total 120) | N/A (Dataset) | *(clean)* | **`VALID`** |
| `datasets/safety_v3_attacks_bash.jsonl` | dataset | 250 | *(none)* | *(none)* | 9 conditions (total 250) | N/A (Dataset) | *(clean)* | **`VALID`** |
| `datasets/safety_v3_attacks_cmd.jsonl` | dataset | 199 | *(none)* | *(none)* | 9 conditions (total 199) | N/A (Dataset) | *(clean)* | **`VALID`** |
| `datasets/safety_v3_attacks_powershell.jsonl` | dataset | 283 | *(none)* | *(none)* | 9 conditions (total 283) | N/A (Dataset) | *(clean)* | **`VALID`** |
| `datasets/safety_v3_sealed.jsonl` | dataset | 100 | *(none)* | *(none)* | 10 conditions (total 100) | N/A (Dataset) | *(clean)* | **`VALID`** |
| `datasets/sealed_test.jsonl` | dataset | 50 | *(none)* | *(none)* | file_ops: 29, read_only: 12, clarification: 9 | N/A (Dataset) | *(clean)* | **`VALID`** |

---

## 2. make_figures.py Dependency Gate Audit

The automated reporting script `scripts/make_figures.py` reads empirical records to compile final tables and charts.
The evidence ledger enforces a **hard gate**: if ANY dependency of `make_figures.py` is invalid, the build fails.

| Dependency File | Role in make_figures.py | File Status | Gate Result |
| :--- | :--- | :---: | :---: |
| `results/safety.jsonl` | Primary Table 8.1 Safety Guard metrics | **`VALID`** | ✅ ALLOWED |
| `results/e0_real.jsonl` | Table 8.2 E0 Prefix Equality Gate | **`VALID`** | ✅ ALLOWED |

**Gate Assessment**: ✅ **ALL make_figures.py DEPENDENCIES VALID**

---

## 3. Detailed Per-File Audit Findings

### 🟢 `results/ablation.jsonl` (VALID)
- **Type**: Result | **Rows**: 3795
- **Backends**: `['llama-server']`
- **Models**: `['NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf']`
- **Provenance Present**: Yes
- **Condition Breakdown**: `{'file_overwrite': 710, 'file_deletion': 695, 'directory_wipe': 475, 'safe_reading': 340, 'file_move': 330, 'safe_searching': 315}`

### 🟢 `results/adaptive_attacks.jsonl` (VALID)
- **Type**: Result | **Rows**: 816
- **Backends**: `['guard-pipeline-g0-g3']`
- **Models**: `['NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf']`
- **Provenance Present**: Yes
- **Condition Breakdown**: `{'file_deletion': 159, 'file_overwrite': 136, 'directory_wipe': 113, 'file_move': 68, 'safe_listing': 68, 'safe_reading': 68}`

### 🔴 `results/context_eval.jsonl` (INVALID)
- **Type**: Result | **Rows**: 120
- **Backends**: `[]`
- **Models**: `[]`
- **Provenance Present**: No
- **Missing Provenance Fields**: `['backend', 'driver', 'git_sha', 'gpu', 'model_file', 'model_sha256', 'run_id', 'seed', 'server_build', 'timestamp']`
- **Flags Raised**: MISSING_PROVENANCE
- **Reasons**: Missing required provenance metadata: ['backend', 'driver', 'git_sha', 'gpu', 'model_file', 'model_sha256', 'run_id', 'seed', 'server_build', 'timestamp']

### 🔴 `results/e0.jsonl` (INVALID)
- **Type**: Result | **Rows**: 50
- **Backends**: `[]`
- **Models**: `[]`
- **Provenance Present**: No
- **Missing Provenance Fields**: `['backend', 'driver', 'git_sha', 'gpu', 'model_file', 'model_sha256', 'run_id', 'seed', 'server_build']`
- **Flags Raised**: MISSING_PROVENANCE
- **Reasons**: Missing required provenance metadata: ['backend', 'driver', 'git_sha', 'gpu', 'model_file', 'model_sha256', 'run_id', 'seed', 'server_build']
- **Condition Breakdown**: `{'read_only': 21, 'state_modifying': 16, 'destructive': 13}`

### 🟢 `results/e0_real.jsonl` (VALID)
- **Type**: Result | **Rows**: 50
- **Backends**: `['lm-studio']`
- **Models**: `['NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf']`
- **Provenance Present**: Yes
- **Condition Breakdown**: `{'read_only': 21, 'state_modifying': 16, 'destructive': 13}`

### 🟢 `results/e5_dev.jsonl` (VALID)
- **Type**: Result | **Rows**: 400
- **Backends**: `['llama-server']`
- **Models**: `['NVIDIA-Nemotron-3-Nano-4B-Dense-Q4_K_M.gguf', 'NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf']`
- **Provenance Present**: Yes
- **Condition Breakdown**: `{'base+full': 100, 'base+short': 100, 'tuned+full': 100, 'tuned+short': 100}`

### 🟢 `results/e5_matrix.jsonl` (VALID)
- **Type**: Result | **Rows**: 200
- **Backends**: `['llama-server']`
- **Models**: `['NVIDIA-Nemotron-3-Nano-4B-Dense-Q4_K_M.gguf', 'NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf']`
- **Provenance Present**: Yes
- **Condition Breakdown**: `{'base+full': 50, 'base+short': 50, 'tuned+full': 50, 'tuned+short': 50}`

### 🟢 `results/e5_sealed.jsonl` (VALID)
- **Type**: Result | **Rows**: 200
- **Backends**: `['llama-server']`
- **Models**: `['NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf']`
- **Provenance Present**: Yes
- **Condition Breakdown**: `{'base+full': 50, 'base+short': 50, 'tuned+full': 50, 'tuned+short': 50}`

### 🟢 `results/hardware_matrix.jsonl` (VALID)
- **Type**: Result | **Rows**: 3
- **Backends**: `['llama-server-cpu', 'llama-server-cuda', 'llama-server-vulkan']`
- **Models**: `['NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf']`
- **Provenance Present**: Yes

### 🔴 `results/latency_breakdown.jsonl` (INVALID)
- **Type**: Result | **Rows**: 200
- **Backends**: `['mock']`
- **Models**: `['NVIDIA-Nemotron-3-Nano-4B-Dense-Q4_K_M.gguf']`
- **Provenance Present**: Yes
- **Flags Raised**: MOCK_OR_DEMO_BACKEND
- **Reasons**: Contains mock/demo/simulated backend: 'mock'
- **Condition Breakdown**: `{'reasoning_on': 100, 'reasoning_off': 100}`

### 🟢 `results/learned_escalation.jsonl` (VALID)
- **Type**: Result | **Rows**: 16
- **Backends**: `['learned-escalation-g5']`
- **Models**: `['NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf']`
- **Provenance Present**: Yes

### 🟢 `results/safety.jsonl` (VALID)
- **Type**: Result | **Rows**: 73
- **Backends**: `['static_analysis']`
- **Models**: `['effects_engine_v1']`
- **Provenance Present**: Yes
- **Condition Breakdown**: `{'benign_look_alikes': 10, 'direct': 8, 'benign_read_only': 8, 'alternate_tool': 6, 'indirection': 6, 'benign_state_modifying': 6}`

### 🔴 `results/safety_v2.jsonl` (INVALID)
- **Type**: Result | **Rows**: 1200
- **Backends**: `[]`
- **Models**: `[]`
- **Provenance Present**: No
- **Missing Provenance Fields**: `['backend', 'driver', 'gpu', 'model_file', 'model_sha256', 'seed', 'server_build', 'timestamp']`
- **Flags Raised**: MISSING_PROVENANCE
- **Reasons**: Missing required provenance metadata: ['backend', 'driver', 'gpu', 'model_file', 'model_sha256', 'seed', 'server_build', 'timestamp']
- **Condition Breakdown**: `{'G0-simple': 240, 'G0-published': 240, 'G1': 240, 'G2-metrics': 240, 'G3': 240}`

### 🟢 `results/safety_v3.jsonl` (VALID)
- **Type**: Result | **Rows**: 4392
- **Backends**: `['llama-server']`
- **Models**: `['NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf']`
- **Provenance Present**: Yes
- **Condition Breakdown**: `{'G0-simple': 732, 'G0-published': 732, 'G1': 732, 'G2': 732, 'G3': 732, 'G4': 732}`

### 🟢 `results/sealed_audit.jsonl` (VALID)
- **Type**: Audit | **Rows**: 1
- **Backends**: `[]`
- **Models**: `[]`
- **Provenance Present**: Yes

### 🟢 `results/sealed_final.jsonl` (VALID)
- **Type**: Result | **Rows**: 300
- **Backends**: `['guard-pipeline-g0-g3', 'llama-server']`
- **Models**: `['NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf']`
- **Provenance Present**: Yes
- **Condition Breakdown**: `{'base+full': 50, 'base+short': 50, 'tuned+full': 50, 'tuned+short': 50, 'file_deletion': 21, 'file_overwrite': 20}`

### 🟢 `datasets/attacks_v3_bash.jsonl` (VALID)
- **Type**: Dataset | **Rows**: 250
- **Backends**: `[]`
- **Models**: `[]`
- **Provenance Present**: No
- **Condition Breakdown**: `{'file_deletion': 58, 'file_overwrite': 44, 'file_truncation': 28, 'safe_reading': 23, 'file_move': 22, 'safe_searching': 22}`

### 🟢 `datasets/attacks_v3_cmd.jsonl` (VALID)
- **Type**: Dataset | **Rows**: 199
- **Backends**: `[]`
- **Models**: `[]`
- **Provenance Present**: No
- **Condition Breakdown**: `{'file_overwrite': 46, 'file_deletion': 31, 'directory_wipe': 30, 'safe_reading': 18, 'file_move': 17, 'safe_searching': 17}`

### 🟢 `datasets/attacks_v3_powershell.jsonl` (VALID)
- **Type**: Dataset | **Rows**: 283
- **Backends**: `[]`
- **Models**: `[]`
- **Provenance Present**: No
- **Condition Breakdown**: `{'file_overwrite': 52, 'file_deletion': 50, 'directory_wipe': 46, 'file_move': 27, 'safe_reading': 27, 'safe_searching': 24}`

### 🟢 `datasets/benign_commands.jsonl` (VALID)
- **Type**: Dataset | **Rows**: 120
- **Backends**: `[]`
- **Models**: `[]`
- **Provenance Present**: No
- **Condition Breakdown**: `{'benign_read_only': 66, 'benign_look_alike': 30, 'benign_state_modifying': 24}`

### 🟢 `datasets/benign_v3.jsonl` (VALID)
- **Type**: Dataset | **Rows**: 317
- **Backends**: `[]`
- **Models**: `[]`
- **Provenance Present**: No
- **Condition Breakdown**: `{'safe_reading': 68, 'safe_searching': 63, 'safe_listing': 58, 'safe_creation': 44, 'file_overwrite': 25, 'file_deletion': 24}`

### 🟢 `datasets/context_eval_cases.jsonl` (VALID)
- **Type**: Dataset | **Rows**: 20
- **Backends**: `[]`
- **Models**: `[]`
- **Provenance Present**: No

### 🟢 `datasets/dev_v3.jsonl` (VALID)
- **Type**: Dataset | **Rows**: 100
- **Backends**: `[]`
- **Models**: `[]`
- **Provenance Present**: No
- **Condition Breakdown**: `{'file_ops': 48, 'read_only': 30, 'clarification': 15, 'process_mgmt': 7}`

### 🟢 `datasets/e5_sealed.jsonl` (VALID)
- **Type**: Dataset | **Rows**: 50
- **Backends**: `[]`
- **Models**: `[]`
- **Provenance Present**: No
- **Condition Breakdown**: `{'file_ops': 29, 'read_only': 12, 'clarification': 9}`

### 🟢 `datasets/finetune_train.jsonl` (VALID)
- **Type**: Dataset | **Rows**: 7200
- **Backends**: `[]`
- **Models**: `[]`
- **Provenance Present**: No
- **Condition Breakdown**: `{'file_ops': 2391, 'process_control': 1139, 'sys_info': 1078, 'git_vcs': 1038, 'networking': 827, 'clarification': 727}`

### 🟢 `datasets/finetune_v2_train.jsonl` (VALID)
- **Type**: Dataset | **Rows**: 10800
- **Backends**: `[]`
- **Models**: `[]`
- **Provenance Present**: No
- **Condition Breakdown**: `{'file_ops': 4173, 'read_only': 3251, 'process_mgmt': 1726, 'clarification': 972, 'git_vcs': 678}`

### 🟢 `datasets/finetune_v2_val.jsonl` (VALID)
- **Type**: Dataset | **Rows**: 1200
- **Backends**: `[]`
- **Models**: `[]`
- **Provenance Present**: No
- **Condition Breakdown**: `{'file_ops': 472, 'read_only': 372, 'process_mgmt': 173, 'clarification': 108, 'git_vcs': 75}`

### 🟢 `datasets/finetune_val.jsonl` (VALID)
- **Type**: Dataset | **Rows**: 800
- **Backends**: `[]`
- **Models**: `[]`
- **Provenance Present**: No
- **Condition Breakdown**: `{'file_ops': 268, 'process_control': 132, 'git_vcs': 127, 'sys_info': 112, 'networking': 88, 'clarification': 73}`

### 🟢 `datasets/safety_bench.jsonl` (VALID)
- **Type**: Dataset | **Rows**: 73
- **Backends**: `[]`
- **Models**: `[]`
- **Provenance Present**: No
- **Condition Breakdown**: `{'benign_look_alikes': 10, 'direct': 8, 'benign_read_only': 8, 'alternate_tool': 6, 'indirection': 6, 'benign_state_modifying': 6}`

### 🟢 `datasets/safety_bench_v2_attacks.jsonl` (VALID)
- **Type**: Dataset | **Rows**: 120
- **Backends**: `[]`
- **Models**: `[]`
- **Provenance Present**: No
- **Condition Breakdown**: `{'direct': 32, 'alternate_tool': 16, 'truncation_redirect': 16, 'interpreter_oneliner': 12, 'indirection': 12, 'privileged': 9}`

### 🟢 `datasets/safety_v3_attacks_bash.jsonl` (VALID)
- **Type**: Dataset | **Rows**: 250
- **Backends**: `[]`
- **Models**: `[]`
- **Provenance Present**: No
- **Condition Breakdown**: `{'file_deletion': 58, 'file_overwrite': 44, 'file_truncation': 28, 'safe_reading': 23, 'file_move': 22, 'safe_searching': 22}`

### 🟢 `datasets/safety_v3_attacks_cmd.jsonl` (VALID)
- **Type**: Dataset | **Rows**: 199
- **Backends**: `[]`
- **Models**: `[]`
- **Provenance Present**: No
- **Condition Breakdown**: `{'file_overwrite': 46, 'file_deletion': 31, 'directory_wipe': 30, 'safe_reading': 18, 'file_move': 17, 'safe_searching': 17}`

### 🟢 `datasets/safety_v3_attacks_powershell.jsonl` (VALID)
- **Type**: Dataset | **Rows**: 283
- **Backends**: `[]`
- **Models**: `[]`
- **Provenance Present**: No
- **Condition Breakdown**: `{'file_overwrite': 52, 'file_deletion': 50, 'directory_wipe': 46, 'file_move': 27, 'safe_reading': 27, 'safe_searching': 24}`

### 🟢 `datasets/safety_v3_sealed.jsonl` (VALID)
- **Type**: Dataset | **Rows**: 100
- **Backends**: `[]`
- **Models**: `[]`
- **Provenance Present**: No
- **Condition Breakdown**: `{'file_deletion': 21, 'file_overwrite': 20, 'directory_wipe': 13, 'safe_listing': 10, 'file_move': 9, 'safe_searching': 9}`

### 🟢 `datasets/sealed_test.jsonl` (VALID)
- **Type**: Dataset | **Rows**: 50
- **Backends**: `[]`
- **Models**: `[]`
- **Provenance Present**: No
- **Condition Breakdown**: `{'file_ops': 29, 'read_only': 12, 'clarification': 9}`
