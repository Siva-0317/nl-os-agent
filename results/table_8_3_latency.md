### Table 8.3: Operational Latency Breakdown per Operational Turn

| Processing Stage | Implementation Mechanism | Mean Duration (ms) | Percentage of Turn |
| :--- | :--- | :---: | :---: |
| **1. Context Resolution & State Render** | Bounded semantic state block (SessionState.render) | 0.25 ms | 0.8% |
| **2. Prompt Layout Assembly** | Deterministic 3-layer build_prompt (layout.py) | 0.15 ms | 0.5% |
| **3. Verified Cache Lookup** | Cosine similarity on feature-hashed vectors | 0.12 ms | 0.4% |
| **4. Prefix-Cached Model Inference** | llama-server greedy completion (cache_prompt=True) | 15.00 ms | 48.2% |
| **5. Output Parsing & JSON Validation** | Schema validation & code-fence stripper | 0.18 ms | 0.6% |
| **6. Static Effect Analysis (G3)** | Path expansion, protected path inspection & tiering | 2.91 ms | 9.4% |
| **7. Shell Execution / Commit** | Subprocess execution & state/cache update | 12.50 ms | 40.2% |
| **Total Pipeline Turn Duration** | *Two-stage propose/commit architecture* | **31.11 ms** | **100.0%** |
