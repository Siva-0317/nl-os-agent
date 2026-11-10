# NL-OS-Agent Knowledge Vault Index (`wiki/index.md`)

Welcome to the master catalog of the **NL-OS-Agent** research and engineering repository.

---

## 1. Quick Navigation
- **Active Hot Context**: [`wiki/hot.md`](hot.md)
- **Vault Governance**: [`AGENTS.md`](../AGENTS.md)
- **Changelog**: [`wiki/log.md`](log.md)
- **Raw Sources**: [`raw/README.md`](../raw/README.md)
- **Evidence Ledger**: [`docs/evidence_ledger.md`](../../docs/evidence_ledger.md)

---

## 2. Decision Records (`wiki/decisions/`)
Key architectural and engineering trade-offs:
- [`D01: Bundled llama-server vs LM Studio or Python Bindings`](decisions/D01_standalone_llama_server.md)
- [`D02: Cross-Shell Native AST Parsers (PowerShell & cmd.exe)`](decisions/D02_windows_native_safety.md)

---

## 3. Experiment Cards (`wiki/experiments/`)
Methodology, configurations, metrics, and empirical results:
- [`E0: Prefix Cache Latency and KV State Reuse`](experiments/E0_prefix_cache_latency.md)
- [`E-Safety: Benchmark v3 Multi-Guard Evaluation`](experiments/E_safety_bench_v3.md)

---

## 4. Claim Ladders (`wiki/claims/`)
Formal research claims and supporting evidence pointers:
- [`C1: Low Latency on Resource-Constrained CPU Tiers`](claims/C1_low_latency_cpu.md)
- [`C2: Zero Unconfirmed Harm Guarantee`](claims/C2_zero_unconfirmed_harm.md)

---

## 5. Prior Art & Baselines (`wiki/prior_art/`)
Comparative analysis and porting of baseline command safety guards:
- [`Open-Source Command Approval & Guard Baselines`](prior_art/open_source_baselines.md)

