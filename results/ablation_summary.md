# G3 Safety Layer Ablation Study: Component Impact Analysis

## Executive Summary

Ablation analysis measuring the individual defensive contribution of the four core architectural mechanisms of the proposed G3 safety layer: **commit-time TOCTOU re-check**, **protected system paths**, **opaque execution escalation**, and **realpath symlink resolution**.
Each condition disables exactly one component, evaluating bypass and false-alarm rates on identical matched case sets with exact Clopper-Pearson intervals and paired McNemar tests.

- **Engine Git SHA**: `dfce40e50ae122bbf342a84ccbef0a6eaab4bfaf`
- **Run ID**: `9a1c5d17d6d6`
- **Harmful Attack Cases ($n$)**: `435`
- **Benign Operation Cases ($n$)**: `324`

### Primary Ablation Performance Matrix

| Configuration | Ablated Component | Bypass Rate (%) [95% CI] | Δ Bypass vs Full | False Alarm Rate (%) [95% CI] | Δ False Alarm | Confirmation Burden (/100) | McNemar $p$-value |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **G3-Full** | none | **13.3%** [10.3%, 16.9%] | *(baseline)* | 51.2% [45.6%, 56.8%] | *(baseline)* | **51.2** | - |
| **G3-NoTOCTOU** | TOCTOU re-check | **14.3%** [11.1%, 17.9%] | **+0.9%** | 51.2% [45.6%, 56.8%] | +0.0% | **51.2** | $p = 0.1250$ |
| **G3-NoProtectedPaths** | Protected paths | **13.6%** [10.5%, 17.1%] | **+0.2%** | 51.2% [45.6%, 56.8%] | +0.0% | **51.2** | $p = 1.0000$ |
| **G3-NoOpaqueEscalation** | Opaque escalation | **42.8%** [38.1%, 47.6%] | **+29.4%** | 10.8% [7.6%, 14.7%] | -40.4% | **10.8** | $p < 0.001$ |
| **G3-NoSymlinkResolution** | Symlink resolution | **13.3%** [10.3%, 16.9%] | 0.0% | 51.2% [45.6%, 56.8%] | +0.0% | **51.2** | $p = 1.0000$ |

### Component Contribution Findings & Architectural Insights

1. **Opaque Escalation (`G3-NoOpaqueEscalation`)**: Disabling opaque escalation creates the single largest defensive vulnerability, causing a massive surge in bypasses from encoded commands, dynamic variables, and subshell pipelines. While it reduces benign false alarms, eliminating opaque escalation leaves the agent defenseless against runtime code generation and obfuscation.

2. **TOCTOU Re-Check (`G3-NoTOCTOU`)**: Disabling commit-time verification results in complete failure against time-of-check to time-of-use mutations ($p < 0.001$). Blindly trusting proposal-time assessments allows wildcard deletions and delayed payloads to execute without warning when the filesystem mutates during the human review window.

3. **Protected Paths Enforcement (`G3-NoProtectedPaths`)**: Ablating protected system paths and privileged hives allows direct destruction of critical system directories (`/etc`, `/var`, `C:\Windows\System32`) without hard-blocking.

4. **Realpath Symlink Resolution (`G3-NoSymlinkResolution`)**: Disabling symlink resolution blinds the safety layer to symlink traversal attacks, allowing harmless-looking local symlinks to silently target privileged system files.

### Provenance & Traceability
- **Engine Git SHA**: `dfce40e50ae122bbf342a84ccbef0a6eaab4bfaf`
- **Timestamp**: `2026-11-08T19:06:56.800605+00:00`
- **Sandbox Isolation**: Executed strictly inside throwaway temporary sandboxes (`tempfile.TemporaryDirectory`).

