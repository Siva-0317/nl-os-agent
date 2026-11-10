"""
tests/test_paper_gate.py - Test Suite for Task T45: Automated Paper Numbers Gate.

Verifies:
1. Evidence ledger parsing (identifying VALID vs INVALID files, row counts, condition stats).
2. Number and metric extraction for both Markdown and LaTeX documents.
3. Exemption filtering for hardware specs, model params, external citations, and scientific notation.
4. Provenance tracking (line-level, table-level, section-level, and comment-level).
5. Ground-truth validation:
   - Valid claims pass with zero violations.
   - Deviated numbers trigger DEVIATES_FROM_DATA.
   - Mock/invalid sources trigger UNAUTHENTICATED_SOURCE.
   - Bare unlinked quantitative claims trigger LACKS_PROVENANCE.
6. End-to-end CLI execution on the repository documentation and paper sources.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure project root & scripts are on sys.path
_repo_root = Path(__file__).resolve().parent.parent
_scripts = _repo_root / "scripts"
for p in (_repo_root, _scripts):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from gate_paper_numbers import (
    ExtractedClaim,
    GateViolation,
    NumberExtractor,
    PaperNumbersGate,
    TruthRegistry,
)


@pytest.fixture
def repo_root() -> Path:
    return _repo_root


@pytest.fixture
def gate(repo_root: Path) -> PaperNumbersGate:
    return PaperNumbersGate(
        repo_root=repo_root,
        ledger_path="docs/evidence_ledger.md",
        tolerance=0.05,
        latency_tolerance=1.0,
        strict=True,
    )


def test_ledger_parsing(gate: PaperNumbersGate):
    """Verify that TruthRegistry correctly parses docs/evidence_ledger.md."""
    reg = gate.registry
    assert len(reg.valid_files) >= 15, "Expected at least 15 valid files in ledger"
    assert "results/sealed_final.jsonl" in reg.valid_files
    assert "results/safety.jsonl" in reg.valid_files
    assert "results/e0_real.jsonl" in reg.valid_files
    assert "results/hardware_matrix.jsonl" in reg.valid_files
    assert "results/learned_escalation.jsonl" in reg.valid_files

    # Check invalid files are caught
    assert "results/e0.jsonl" in reg.invalid_files
    assert "results/context_eval.jsonl" in reg.invalid_files
    assert "results/latency_breakdown.jsonl" in reg.invalid_files
    assert "results/e5_matrix.jsonl" in reg.invalid_files


def test_truth_registry_facts(gate: PaperNumbersGate):
    """Verify that TruthRegistry loads ground-truth facts for valid files."""
    reg = gate.registry

    # Check sealed_final.jsonl facts
    sealed_facts = reg.facts_by_file["results/sealed_final.jsonl"]
    sealed_percentages = [f.value for f in sealed_facts if f.category == "percentage"]
    assert 98.3 in sealed_percentages, "Expected 98.3% Combined G0-G3 interception in facts"
    assert 94.0 in sealed_percentages, "Expected 94.0% Tuned execution accuracy in facts"
    assert 44.0 in sealed_percentages, "Expected 44.0% Base full accuracy in facts"

    sealed_latencies = [f.value for f in sealed_facts if f.category == "latency"]
    assert 24.6 in sealed_latencies, "Expected 24.6 ms Tuned short TTFT in facts"

    # Check safety.jsonl facts
    safety_facts = reg.facts_by_file["results/safety.jsonl"]
    safety_percentages = [f.value for f in safety_facts if f.category == "percentage"]
    assert 0.0 in safety_percentages, "Expected 0.0% G3 bypass in facts"
    assert 49.0 in safety_percentages, "Expected 49.0% G0 bypass in facts"

    # Check e0_real.jsonl facts
    e0_facts = reg.facts_by_file["results/e0_real.jsonl"]
    e0_percentages = [f.value for f in e0_facts if f.category == "percentage"]
    assert 100.0 in e0_percentages, "Expected 100.0% match in e0_real facts"
    e0_latencies = [f.value for f in e0_facts if f.category == "latency"]
    assert 15.0 in e0_latencies, "Expected 15.0 ms cached latency in facts"


def test_number_extractor_markdown():
    """Verify that NumberExtractor parses various quantitative claims in Markdown."""
    line = "Combined defense achieved 98.3% interception with [91.1%, 100.0%] CI at 24.6 ms latency (5.0x speedup)."
    claims = NumberExtractor.extract_from_line(line, 1, "test.md")

    categories = {c.category for c in claims}
    assert "percentage" in categories
    assert "interval" in categories
    assert "latency" in categories
    assert "speedup" in categories

    pct_claim = next(c for c in claims if c.category == "percentage")
    assert pct_claim.numeric_value == 98.3

    interval_claim = next(c for c in claims if c.category == "interval")
    assert interval_claim.numeric_value == (91.1, 100.0)

    latency_claim = next(c for c in claims if c.category == "latency")
    assert latency_claim.numeric_value == 24.6
    assert latency_claim.unit == "ms"

    speedup_claim = next(c for c in claims if c.category == "speedup")
    assert speedup_claim.numeric_value == 5.0


def test_number_extractor_latex():
    """Verify that NumberExtractor handles LaTeX formatting."""
    line = r"Achieved \textbf{94.0\%} execution acc at 24.6~ms ($p = 0.0000$, $p < 0.05$)."
    claims = NumberExtractor.extract_from_line(line, 1, "paper.tex")

    pct_claim = next(c for c in claims if c.category == "percentage")
    assert pct_claim.numeric_value == 94.0

    lat_claim = next(c for c in claims if c.category == "latency")
    assert lat_claim.numeric_value == 24.6

    p_claims = [c for c in claims if c.category == "p_value"]
    assert len(p_claims) >= 1
    assert p_claims[0].numeric_value == 0.0000


def test_exemption_filters():
    """Verify that non-empirical context lines are exempt from verification."""
    # Dates
    assert NumberExtractor.is_exempt_line("# Date: 2026-11-10")
    # Section headers
    assert NumberExtractor.is_exempt_line("### 7.3 Prompts")
    # External literature
    assert NumberExtractor.is_exempt_line("Marconi (MLSys 2025, arXiv:2411.19379) reports 34.4x token hit rate")
    # Planning / sizing
    assert NumberExtractor.is_exempt_line("Build 300 to 500 cases in disposable environments")
    assert NumberExtractor.is_exempt_line("| Clarification examples | 10 to 15 % of data |")
    assert NumberExtractor.is_exempt_line("throughput is ~7.3 seconds per sample")


def test_valid_markdown_document_passes(gate: PaperNumbersGate, tmp_path: Path):
    """Verify that a markdown document with correct provenance and numbers passes with 0 violations."""
    doc = tmp_path / "report.md"
    doc.write_text(
        """# Performance Report
<!-- provenance: results/sealed_final.jsonl -->
Under the frozen sealed test, the agent achieved **98.3%** interception with [91.1%, 100.0%] CI.
The prompt-compressed model achieved **94.0%** accuracy at **24.6 ms** mean TTFT (**5.0x** speedup).
""",
        encoding="utf-8",
    )
    claims, violations = gate.scan_document(doc)
    assert len(claims) >= 4
    assert len(violations) == 0, f"Expected 0 violations, got: {violations}"


def test_valid_latex_document_passes(gate: PaperNumbersGate, tmp_path: Path):
    """Verify that a LaTeX document with valid numbers passes with 0 violations."""
    doc = tmp_path / "paper.tex"
    doc.write_text(
        r"""\begin{document}
% provenance: results/sealed_final.jsonl
The combined defense intercepted \textbf{98.3\%} [91.1\%, 100.0\%] of harmful attacks.
The model achieved \textbf{94.0\%} execution accuracy at 24.6~ms mean latency.
\end{document}
""",
        encoding="utf-8",
    )
    claims, violations = gate.scan_document(doc)
    assert len(claims) >= 3
    assert len(violations) == 0, f"Expected 0 violations, got: {violations}"


def test_deviated_number_triggers_violation(gate: PaperNumbersGate, tmp_path: Path):
    """Verify that a number that deviates from authenticated data triggers DEVIATES_FROM_DATA."""
    doc = tmp_path / "bad_num.md"
    doc.write_text(
        """# Bad Report
<!-- provenance: results/sealed_final.jsonl -->
The defense achieved **80.0%** interception rate.
""",
        encoding="utf-8",
    )
    claims, violations = gate.scan_document(doc)
    assert len(violations) == 1
    assert violations[0].category == "DEVIATES_FROM_DATA"
    assert "deviates from authenticated data" in violations[0].message


def test_unauthenticated_source_triggers_violation(gate: PaperNumbersGate, tmp_path: Path):
    """Verify that citing an INVALID ledger file triggers UNAUTHENTICATED_SOURCE."""
    doc = tmp_path / "invalid_source.md"
    doc.write_text(
        """# Unverified Report
<!-- provenance: results/e0.jsonl -->
Output identity was **100.0%** across 50 prompts (`results/e0.jsonl`).
""",
        encoding="utf-8",
    )
    claims, violations = gate.scan_document(doc)
    assert len(violations) >= 1
    assert violations[0].category == "UNAUTHENTICATED_SOURCE"
    assert "marked INVALID" in violations[0].message


def test_missing_provenance_triggers_violation(gate: PaperNumbersGate, tmp_path: Path):
    """Verify that bare numbers without any provenance link trigger LACKS_PROVENANCE."""
    doc = tmp_path / "no_prov.md"
    doc.write_text(
        """# Unreferenced Claims
We observed that accuracy reached 99.5% on the test suite.
""",
        encoding="utf-8",
    )
    claims, violations = gate.scan_document(doc)
    assert len(violations) == 1
    assert violations[0].category == "LACKS_PROVENANCE"
    assert "lacks an explicit provenance link" in violations[0].message


def test_cli_execution_repo_docs():
    """Verify that python scripts/gate_paper_numbers.py exits 0 on the actual repository documents."""
    cmd = [sys.executable, str(_scripts / "gate_paper_numbers.py")]
    res = subprocess.run(cmd, cwd=str(_repo_root), capture_output=True, text=True)
    assert res.returncode == 0, f"Gate failed with stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    assert "[PASSED] ALL PAPER NUMBERS, LATENCIES, SAMPLE SIZES & INTERVALS VERIFIED." in res.stdout

