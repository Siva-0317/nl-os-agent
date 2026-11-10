# Vault Agent Instructions and Governance (`AGENTS.md`)

Welcome to the **NL-OS-Agent Research Vault**. This vault serves as the persistent memory, decision record, and provenance ledger for all agents and researchers working on this project.

All AI agents, coding assistants, and automated loops MUST strictly adhere to the following rules:

---

## 1. Vault Navigation & Reading Hierarchy

When answering queries, resuming tasks, or exploring past work, always follow this order:
1. **Read `wiki/hot.md` first**: This file contains the active state, current task, latest verified metrics, and immediate open risks (~300–500 words).
2. **Read `wiki/index.md` second**: This is the master catalog of all topic pages, decision records, experiment cards, and claim ladders.
3. **Read ONLY the specific page needed**: Do not ingest the entire vault into context. Follow links to specific files under `decisions/`, `experiments/`, `claims/`, or `prior_art/`.

---

## 2. Quantitative Claims & The Evidence Ledger Rule

> **CRITICAL RULE**: NEVER copy or invent bare benchmark numbers or performance claims in prose without linking directly to the authenticated evidence ledger or results file.

- Every page that states an accuracy, latency, bypass rate, or sample size must include a markdown link to the corresponding entry in [`docs/evidence_ledger.md`](../../docs/evidence_ledger.md) or a `results/*.jsonl` file.
- Example **Valid**:
  ```markdown
  The deterministic effect engine achieved a [0.0% bypass rate on 300 blind attacks](../../results/safety_v3.jsonl) (95% Clopper-Pearson CI: [0.0%, 1.2%]).
  ```
- Example **INVALID** (flagged by `scripts/vault_lint.py`):
  ```markdown
  The model achieves 96.2% accuracy in 12ms.
  ```

---

## 3. Append-Only Logging Rule

- Every change, addition, or refactoring in the vault MUST be logged in [`wiki/log.md`](wiki/log.md).
- Write newest entries **at the top** (`log.md` is strictly newest-first).
- Include:
  - Timestamp (ISO 8601 or YYYY-MM-DD)
  - Agent / Author identity
  - Summary of changes
  - Files created or modified

---

## 4. Immutable Raw Sources Rule

- The [`raw/`](raw/) directory contains raw external papers, legacy handovers, unedited benchmark dumps, and raw survey transcripts.
- **NEVER edit, modify, or overwrite files in `raw/`**. They are read-only historical records.

---

## 5. Standard Page Templates

### A. Experiment Card (`wiki/experiments/E*.md`)
Every experiment card must contain:
- **Hypothesis**: Precise, falsifiable prediction.
- **Configuration**: Git commit SHA, model file name and SHA-256, runtime backend.
- **Dataset & Split**: Dev split vs Sealed test split, sample count ($N$).
- **Primary Metric**: Defined metric with statistical interval (Wilson, Clopper-Pearson, or bootstrap).
- **Result Link**: Markdown link to `results/<file>.jsonl` and row in `docs/evidence_ledger.md`.
- **Decision**: Keep, reject, or iterate.

### B. Decision Record (`wiki/decisions/D*.md`)
Every decision record must contain:
- **Context**: The engineering or scientific dilemma.
- **Options Considered**: Alternatives with trade-offs.
- **Decision**: The selected path.
- **Rationale & Evidence**: Empirical or structural reasons for the choice.
- **Consequences**: What becomes easier, harder, or forbidden.

---

## 6. Vault Integrity Linting

Before concluding any session that modifies the vault, run the automated linter:
```bash
python scripts/vault_lint.py --vault-dir vault
```
Fix all reported unlinked numbers, orphan pages, or missing log entries before committing.

