# Prior Art: Open-Source Command Approval & Guard Baselines

*Category: Literature & Comparative Prior Art*  
*Status: Active Reference*

---

## 1. Overview
Command execution safety in modern coding agents varies widely between heuristic regexes, static allowlists, and pure LLM self-reflection. To establish a rigorous baseline, NLAgent ports and evaluates standard defense strategies implemented across leading open-source agent projects.

---

## 2. Analyzed Baselines
The following guard logic is implemented in [`src/nlagent/safety/baselines/`](../../src/nlagent/safety/baselines/) and tested in [`tests/test_baselines.py`](../../tests/test_baselines.py):

1. **Aider (G0-simple / regex list)**:
   - Evaluates command strings against known safe read-only prefixes.
   - Susceptible to command chaining (`git status && rm -rf /`) and quote escaping.
2. **OpenHands (G0-published / static rules)**:
   - Pattern-based interception targeting common destructive Unix commands (`rm`, `mkfs`, `dd`).
   - Lacks Windows PowerShell / cmd.exe awareness.
3. **SWE-agent & Goose (Execution Hooks)**:
   - Sandboxed container dispatch or interactive prompt confirmation.
   - High confirmation burden on safe developer actions.

---

## 3. Comparison Matrix & Evidence
- Comparative bypass and confirmation metrics are recorded in [`results/safety_v3.jsonl`](../../results/safety_v3.jsonl).
- Formal evidence ledger audit confirms all baselines are evaluated on identical attack sets in [`docs/evidence_ledger.md`](../../docs/evidence_ledger.md).

