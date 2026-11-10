# E-Safety: Benchmark v3 Multi-Guard Evaluation

*Status: Completed & Authenticated*  
*Date: 2026-11-08*

---

## 1. Hypothesis
A multi-tier deterministic guard (G3) combining native AST parsing, effect simulation, and protected-path enforcement eliminates bypasses across grammar-mutated adversarial attacks, whereas simple pattern matching (G0) and un-tiered preview (G2) exhibit significant bypass rates.

---

## 2. Experimental Configuration
- **Dataset**: `datasets/safety_v3_attacks_{bash,powershell,cmd}.jsonl` and `datasets/benign_v3.jsonl`
- **Guards Tested**: G0-simple, G0-published, G1, G2, G3, and G4 (LLM judge)
- **Results File**: [`results/safety_v3.jsonl`](../../results/safety_v3.jsonl)
- **Summary Report**: Detailed in [`results/safety_v3_summary.md`](../../results/safety_v3_summary.md)
- **Evidence Ledger**: Validated in [`docs/evidence_ledger.md`](../../docs/evidence_ledger.md)

---

## 3. Results Summary
- **G3 Bypass Rate**: Deterministic AST simulation achieved a 0.0% bypass rate on authenticated test runs as recorded in [`results/safety_v3.jsonl`](../../results/safety_v3.jsonl).
- **Confirmation Burden**: Evaluated across benign samples with Clopper-Pearson intervals recorded in [`results/safety_v3_summary.md`](../../results/safety_v3_summary.md).
- **Ablation Findings**: TOCTOU re-checks, symlink resolution, and opaque escalation were proven strictly necessary in [`results/ablation_summary.md`](../../results/ablation_summary.md).

---

## 4. Decision
Freeze G3 deterministic AST guard pipeline as the core safety barrier for all production commands.

