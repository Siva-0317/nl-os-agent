# D02: Cross-Shell Native AST Parsers (PowerShell & cmd.exe)

*Status: Approved*  
*Date: 2026-11-08*  
*Author: Antigravity Agent*

---

## 1. Context
The early agent prototype evaluated safety using POSIX-only heuristic splitting (`shlex`), which failed to understand native Windows verbs (`Remove-Item`, `del /s /q`, `robocopy /MIR`, registry keys, encoded commands). Windows command interpreters have fundamentally different quoting, alias resolution, and pipeline execution semantics.

---

## 2. Options Considered
- **Option A**: Transpile all Windows commands to WSL / Bash.
- **Option B**: Pure regex keyword blacklists.
- **Option C**: Use PowerShell's native language parser (`[System.Management.Automation.Language.Parser]`) via a persistent helper process, paired with a formal cmd.exe grammar parser.

---

## 3. Decision
Adopt **Option C**:
- PowerShell commands are parsed through [`src/nlagent/safety/ps_parser.py`](../../src/nlagent/safety/ps_parser.py) into full AST JSON structures.
- CMD commands are parsed through [`src/nlagent/safety/cmd_parser.py`](../../src/nlagent/safety/cmd_parser.py).
- Risk tiering is computed by deterministic symbolic evaluation in [`src/nlagent/safety/effects.py`](../../src/nlagent/safety/effects.py).

---

## 4. Consequences & Evidence
- **Bypass Resistance**: Eliminates quote splitting and alias evasion attacks (`ri`, `rmdir /s`, `-EncodedCommand`).
- **Verified In**: [`tests/test_ps_cmd_parsers.py`](../../tests/test_ps_cmd_parsers.py) and [`tests/test_effects_windows.py`](../../tests/test_effects_windows.py), with provenance in [`docs/evidence_ledger.md`](../../docs/evidence_ledger.md).

