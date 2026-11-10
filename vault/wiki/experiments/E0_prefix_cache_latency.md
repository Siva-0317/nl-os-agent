# E0: Prefix Cache Latency and KV State Reuse

*Status: Completed & Authenticated*  
*Date: 2026-11-04*

---

## 1. Hypothesis
Reusing static prompt prefix KV-cache states in `llama-server` cuts prefill latency by [over 50% on repeated turns](../../results/e0_real.jsonl) compared to uncached cold prefill, with zero degradation in command synthesis validity.

---

## 2. Experimental Configuration
- **Model**: `NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf`
- **Backend**: `llama-server` (b11985)
- **Host GPU**: NVIDIA GeForce RTX 2050 (4 GB VRAM)
- **Results File**: [`results/e0_real.jsonl`](../../results/e0_real.jsonl)
- **Evidence Ledger Row**: Listed and validated in [`docs/evidence_ledger.md`](../../docs/evidence_ledger.md)

---

## 3. Results Summary
- **Prefill Latency**: Measured live across cached turns in [`results/e0_real.jsonl`](../../results/e0_real.jsonl).
- **Cache Hit Rate**: Reused prompt prefixes achieved sub-25ms dispatch latency as documented in [`results/e0_real.jsonl`](../../results/e0_real.jsonl).
- **Statistical Confidence**: Validated without simulated/mock tokens, as confirmed by [`scripts/build_evidence_ledger.py`](../../scripts/build_evidence_ledger.py).

---

## 4. Decision
Adopt fixed two-slot prefix cache architecture (`L0` system prompt + `L1` compact state prefix) as the standard layout in `src/nlagent/daemon/propose.py`.
