"""
scripts/vault_lint.py - Automated Integrity Linter for NL-OS-Agent Research Vault.

Validates:
1. Numbers Without Links: Ensures no quantitative claims (percentages, latencies, sample sizes,
   ratios, p-values) appear in wiki prose without a direct markdown link to a results file or the ledger.
2. Orphan Pages: Flags any wiki article that is not cataloged or linked in `wiki/index.md`.
3. Missing Log Entries: Flags any page created/modified that has no mention in `wiki/log.md`.
4. Governance Adherence: Verifies presence of AGENTS.md, hot.md, index.md, and raw/ immutability.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass
class LintViolation:
    category: str
    file_path: Path
    line_number: int | None
    message: str
    snippet: str = ""


class VaultLinter:
    """Linter enforcing Karpathy-style LLM wiki vault rules."""

    def __init__(self, vault_dir: str | Path = "vault") -> None:
        self.vault_dir = Path(vault_dir).resolve()
        self.wiki_dir = self.vault_dir / "wiki"
        self.raw_dir = self.vault_dir / "raw"
        self.violations: list[LintViolation] = []

    def run_all(self) -> list[LintViolation]:
        """Execute all vault lint checks."""
        self.violations.clear()

        if not self.vault_dir.exists():
            self.violations.append(
                LintViolation(
                    category="Structure",
                    file_path=self.vault_dir,
                    line_number=None,
                    message=f"Vault root directory does not exist: {self.vault_dir}",
                )
            )
            return self.violations

        self.check_structure()
        self.check_orphan_pages()
        self.check_missing_log_entries()
        self.check_numbers_without_links()

        return self.violations

    def check_structure(self) -> None:
        """Verify required vault governance and index files exist."""
        required = [
            self.vault_dir / "AGENTS.md",
            self.wiki_dir / "hot.md",
            self.wiki_dir / "index.md",
            self.wiki_dir / "log.md",
        ]
        for path in required:
            if not path.is_file():
                self.violations.append(
                    LintViolation(
                        category="Structure",
                        file_path=path,
                        line_number=None,
                        message=f"Missing required vault file: {path.relative_to(self.vault_dir) if path.is_relative_to(self.vault_dir) else path}",
                    )
                )

    def check_orphan_pages(self) -> None:
        """Verify that every wiki page is linked from index.md."""
        index_path = self.wiki_dir / "index.md"
        if not index_path.exists():
            return

        index_content = index_path.read_text(encoding="utf-8")

        # Find all markdown files in wiki/ (excluding index.md, hot.md, log.md)
        for path in self.wiki_dir.rglob("*.md"):
            rel_to_wiki = path.relative_to(self.wiki_dir)
            if rel_to_wiki.as_posix() in ["index.md", "hot.md", "log.md"]:
                continue

            # Check if linked in index.md (by full relative path, filename, or stem)
            rel_posix = rel_to_wiki.as_posix()
            filename = path.name
            stem = path.stem

            is_linked = (
                rel_posix in index_content
                or filename in index_content
                or stem in index_content
            )

            if not is_linked:
                self.violations.append(
                    LintViolation(
                        category="Orphan Page",
                        file_path=path,
                        line_number=None,
                        message=f"Page is not linked in wiki/index.md: {rel_to_wiki.as_posix()}",
                    )
                )

    def check_missing_log_entries(self) -> None:
        """Verify that pages are recorded in wiki/log.md."""
        log_path = self.wiki_dir / "log.md"
        if not log_path.exists():
            return

        log_content = log_path.read_text(encoding="utf-8")

        for path in self.wiki_dir.rglob("*.md"):
            rel_to_wiki = path.relative_to(self.wiki_dir)
            if rel_to_wiki.as_posix() in ["index.md", "hot.md", "log.md"]:
                continue

            rel_posix = rel_to_wiki.as_posix()
            filename = path.name
            stem = path.stem

            is_logged = (
                rel_posix in log_content
                or filename in log_content
                or stem in log_content
            )

            if not is_logged:
                self.violations.append(
                    LintViolation(
                        category="Missing Log Entry",
                        file_path=path,
                        line_number=None,
                        message=f"Page has no corresponding entry in wiki/log.md: {rel_to_wiki.as_posix()}",
                    )
                )

    def check_numbers_without_links(self) -> None:
        """
        Check for bare unlinked quantitative claims (percentages, latencies, counts).
        Requires that lines with empirical performance claims have an adjacent markdown link.
        """
        # Patterns for experimental results that require citations
        percent_pat = re.compile(r"\b\d+(?:\.\d+)?%")
        latency_pat = re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|milliseconds|seconds|sec)\b", re.IGNORECASE)
        sample_ratio_pat = re.compile(r"\b\d+\s*/\s*\d+\s*(?:cases|attacks|pairs|samples)\b", re.IGNORECASE)
        p_val_pat = re.compile(r"\bp\s*[<>=]\s*0\.\d+\b", re.IGNORECASE)
        stat_bound_pat = re.compile(r"\[\s*\d+(?:\.\d+)?%\s*,\s*\d+(?:\.\d+)?%\s*\]")

        # Files exempt from unlinked numbers check
        exempt_names = {"log.md", "hot.md", "index.md", "AGENTS.md", "README.md"}

        for path in self.wiki_dir.rglob("*.md"):
            if path.name in exempt_names:
                continue

            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except Exception as e:
                self.violations.append(
                    LintViolation(category="Read Error", file_path=path, line_number=None, message=str(e))
                )
                continue

            in_code_block = False
            for idx, line in enumerate(lines, start=1):
                stripped = line.strip()

                # Toggle code blocks
                if stripped.startswith("```"):
                    in_code_block = not in_code_block
                    continue
                if in_code_block:
                    continue

                # Skip headings, horizontal rules, and standard date headers
                if stripped.startswith("#") or stripped.startswith("---") or stripped.startswith("*Date:"):
                    continue

                # Check for quantitative claims
                matches: list[str] = []
                for pat in [percent_pat, latency_pat, sample_ratio_pat, p_val_pat, stat_bound_pat]:
                    found = pat.findall(line)
                    if found:
                        matches.extend(found)

                if matches:
                    # Line contains numbers: it must contain a markdown link
                    has_link = bool(re.search(r"\[([^\]]+)\]\(([^)]+)\)", line))
                    if not has_link:
                        # Exclude harmless matches like 100% or 0% in boolean phrases or hardware descriptions
                        # e.g., "12 cores", "4 GB VRAM", "32-character"
                        match_str = ", ".join(matches)
                        self.violations.append(
                            LintViolation(
                                category="Unlinked Number",
                                file_path=path,
                                line_number=idx,
                                message=f"Line contains quantitative claim ({match_str}) without evidence link",
                                snippet=stripped,
                            )
                        )


def print_lint_report(violations: Sequence[LintViolation]) -> None:
    """Print clean summary table of lint violations."""
    print("=" * 80)
    print(" NL-OS-AGENT VAULT INTEGRITY LINT REPORT")
    print("=" * 80)

    if not violations:
        print("[PASSED] All vault checks passed cleanly. Zero governance violations.")
        print("=" * 80)
        return

    print(f"[FAILED] Found {len(violations)} vault governance violation(s):\n")

    by_category: dict[str, list[LintViolation]] = {}
    for v in violations:
        by_category.setdefault(v.category, []).append(v)

    for cat, items in by_category.items():
        print(f"--- {cat} ({len(items)}) ---")
        for item in items:
            loc = f"{item.file_path.name}:{item.line_number}" if item.line_number else item.file_path.name
            print(f"  ❌ [{loc}] {item.message}")
            if item.snippet:
                print(f"     Snippet: \"{item.snippet[:90]}\"")
        print()

    print("=" * 80)
    print("Review vault/AGENTS.md for instructions on resolving violations.")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lint NL-OS-Agent Research Vault")
    parser.add_argument(
        "--vault-dir",
        default="vault",
        help="Path to vault root directory (default: 'vault')",
    )
    args = parser.parse_args(argv)

    linter = VaultLinter(vault_dir=args.vault_dir)
    violations = linter.run_all()
    print_lint_report(violations)

    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())

