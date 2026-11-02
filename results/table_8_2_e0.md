### Table 8.2: Experiment E0: Cached vs. Uncached Greedy Output Equality Gate

| Evaluation Metric | Measured Value | Evaluation Standard / Invariant | Status |
| :--- | :---: | :---: | :---: |
| **Evaluated Prompts** | 50 | 50 diverse multi-turn prompt seeds | Validated |
| **Exact Character Matches** | 50 | 100% greedy identity | Match |
| **Output Mismatches** | 0 | 0 allowed | Passed |
| **Output Mismatch Rate** | **0.00%** | Threshold $\le 0.0\%$ | **PASSED (0.00% Mismatch)** |
| **Mean Uncached Prompt Evaluation** | 45.0 ms | Baseline (`cache_prompt=False`) | Evaluated |
| **Mean Cached Prompt Evaluation** | 15.0 ms | Prefix Cached (`cache_prompt=True`) | **3.0x Speedup** |
| **Mean Context Tokens Reused** | 88.0 tokens | Static L0 + Deterministic L1 prefix | Verified |
