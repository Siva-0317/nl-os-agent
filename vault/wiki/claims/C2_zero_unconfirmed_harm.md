# C2: Zero Unconfirmed Harm Guarantee

*Claim Tier: Empirical / Security & Safety Verification*  
*Status: Verified on Blind Benchmark v3*

---

## 1. Formal Claim Statement
Under grammar-mutated adversarial attack datasets spanning Bash, PowerShell, and cmd.exe, the deterministic AST effect engine permits zero harmful state-modifying or destructive executions without explicit, interactive user confirmation or quarantine backup.

---

## 2. Supporting Evidence & Ledger Links
- **Safety Benchmark v3 Results**: Recorded with Clopper-Pearson confidence bounds in [`results/safety_v3.jsonl`](../../results/safety_v3.jsonl) and summarized in [`results/safety_v3_summary.md`](../../results/safety_v3_summary.md).
- **Component Ablation Proofs**: Validated under [`results/ablation.jsonl`](../../results/ablation.jsonl) demonstrating the necessity of TOCTOU re-checks and opaque command escalation.
- **Ledger Verification**: Verified as valid and authenticated in [`docs/evidence_ledger.md`](../../docs/evidence_ledger.md).

---

## 3. Pending Verification Milestones
- Run final frozen evaluation on the sealed test partition (`datasets/safety_v3_sealed.jsonl`) prior to final paper release.

