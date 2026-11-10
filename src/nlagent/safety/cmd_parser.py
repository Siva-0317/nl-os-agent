"""
cmd_parser.py - cmd.exe Command Line Parser.

Parses native Windows cmd.exe command lines, compound statements (&, &&, ||, |),
switches (/S, /F, /Q, /MIR, /Y, /PID, /IM), redirections (> and >>), and standard
cmd.exe verbs (del, rd, move, copy, xcopy, robocopy, reg, taskkill, format, diskpart).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

CMD_VERB_ALIASES: dict[str, str] = {
    "erase": "del",
    "delete": "del",
    "rmdir": "rd",
    "ren": "move",
    "rename": "move",
    "xcopy": "copy",
    "tskill": "taskkill",
}


@dataclass
class CmdInvocation:
    """Represents a single command invocation parsed from a cmd.exe statement."""

    raw_command: str
    verb: str = ""
    subverb: str = ""
    switches: set[str] = field(default_factory=set)
    switch_args: dict[str, str] = field(default_factory=dict)
    args: list[str] = field(default_factory=list)
    redirections: list[tuple[str, str]] = field(default_factory=list)
    raw_tokens: list[str] = field(default_factory=list)

    @property
    def normalized_verb(self) -> str:
        """Return canonical cmd.exe verb (e.g. erase -> del, rmdir -> rd)."""
        v = self.verb.lower()
        if v.endswith(".exe"):
            v = v[:-4]
        return CMD_VERB_ALIASES.get(v, v)

    @property
    def is_recursive(self) -> bool:
        """Check if recursion flag (/S or /s) is present."""
        return "/s" in self.switches or "-s" in self.switches

    @property
    def is_force(self) -> bool:
        """Check if force flag (/F, /Y, /Q) is present."""
        return bool(self.switches.intersection({"/f", "-f", "/y", "-y", "/q", "-q"}))

    @property
    def is_mirror(self) -> bool:
        """Check if robocopy /MIR or /PURGE is present."""
        return bool(self.switches.intersection({"/mir", "-mir", "/purge", "-purge"}))


def _tokenize_cmd_segment(seg: str) -> tuple[list[str], list[tuple[str, str]]]:
    """
    Tokenize a single cmd.exe statement segment, extracting tokens and redirections.
    Handles double quotes and caret (^) escaping.
    """
    tokens: list[str] = []
    redirections: list[tuple[str, str]] = []

    # First extract redirections like >> file or > file
    # We look for >> or > not inside double quotes
    in_quotes = False
    cleaned_chars: list[str] = []
    i = 0
    n = len(seg)

    while i < n:
        c = seg[i]
        if c in ('"', "'"):
            in_quotes = not in_quotes
            cleaned_chars.append(c)
            i += 1
        elif c == "^" and i + 1 < n:
            # Caret escape in cmd.exe
            cleaned_chars.append(seg[i + 1])
            i += 2
        elif not in_quotes and (c == ">" or (c in "12" and i + 1 < n and seg[i + 1] == ">")):
            # Redirection operator
            redir_op = ">"
            if c in "12":
                i += 1
            if i + 1 < n and seg[i + 1] == ">":
                redir_op = ">>"
                i += 2
            else:
                i += 1

            # Consume whitespace
            while i < n and seg[i].isspace():
                i += 1

            # Extract target token (quoted or bare)
            tgt_chars: list[str] = []
            if i < n and seg[i] == '"':
                i += 1
                while i < n and seg[i] != '"':
                    tgt_chars.append(seg[i])
                    i += 1
                if i < n and seg[i] == '"':
                    i += 1
            else:
                while i < n and not seg[i].isspace() and seg[i] not in ("&", "|", ">", "<"):
                    tgt_chars.append(seg[i])
                    i += 1
            target = "".join(tgt_chars).strip()
            if target:
                redirections.append((redir_op, target))
        else:
            cleaned_chars.append(c)
            i += 1

    cleaned_str = "".join(cleaned_chars).strip()

    # Tokenize words respecting double and single quotes
    pattern = r'[^\s"\']+|"[^"]*"|\'[^\']*\''
    raw_matches = re.findall(pattern, cleaned_str)
    tokens = [m.strip('\'"') for m in raw_matches if m.strip()]

    return tokens, redirections


def _split_cmd_statements(command: str) -> list[str]:
    """Split a command line on compound operators (&, &&, ||, |, ;) outside quotes."""
    segments: list[str] = []
    current: list[str] = []
    in_quotes = False
    i = 0
    n = len(command)

    while i < n:
        c = command[i]
        if c in ('"', "'"):
            in_quotes = not in_quotes
            current.append(c)
            i += 1
        elif c == "^" and i + 1 < n:
            current.append(c)
            current.append(command[i + 1])
            i += 2
        elif not in_quotes and c in ("&", "|", ";"):
            # Check for double && or ||
            if i + 1 < n and command[i + 1] == c:
                i += 2
            else:
                i += 1
            seg = "".join(current).strip()
            if seg:
                segments.append(seg)
            current = []
        else:
            current.append(c)
            i += 1

    final_seg = "".join(current).strip()
    if final_seg:
        segments.append(final_seg)

    return segments


# Value switches mapped per command verb so flags like -u in `shred -u file` aren't misparsed
_VERB_VALUE_SWITCHES: dict[str, set[str]] = {
    "taskkill": {"/im", "-im", "/pid", "-pid", "/u", "-u", "/p", "-p", "/fi", "-fi"},
    "tskill": {"/v", "-v", "/a", "-a"},
    "runas": {"/u", "-u", "/user", "-user"},
    "reg": {"/v", "-v", "/ve", "-ve", "/va", "-va", "/t", "-t", "/d", "-d"},
    "format": {"/fs", "-fs"},
    "diskpart": {"/s", "-s"},
}


def parse_cmd_line(command: str) -> list[CmdInvocation]:
    """
    Parse a full cmd.exe command line into a list of CmdInvocation structures.
    """
    invocations: list[CmdInvocation] = []
    segments = _split_cmd_statements(command)

    for seg in segments:
        tokens, redirs = _tokenize_cmd_segment(seg)
        if not tokens and not redirs:
            continue

        raw_verb = tokens[0].strip("'\"") if tokens else ""
        # Handle command-splitting backslashes like r\m
        if not ("/" in raw_verb or ("\\" in raw_verb and (":" in raw_verb or raw_verb.startswith(".\\")))):
            raw_verb = raw_verb.replace("\\", "")
        # Remove path prefix if present e.g. C:\Windows\System32\cmd.exe -> cmd.exe
        verb_base = os.path.basename(raw_verb.replace("\\", "/")).lower() if raw_verb else ""
        if verb_base.endswith(".exe"):
            verb_base = verb_base[:-4]

        inv = CmdInvocation(
            raw_command=seg,
            verb=verb_base,
            redirections=redirs,
            raw_tokens=tokens,
        )

        i = 1
        while i < len(tokens):
            tok = tokens[i]
            lower_tok = tok.lower()

            # Check if this token is a path starting with slash (e.g. /etc/hosts, /usr/bin)
            is_posix_path = lower_tok.startswith("/") and (
                lower_tok.count("/") > 1
                or any(lower_tok.startswith(p) for p in ("/etc", "/usr", "/bin", "/boot", "/var", "/lib", "/sbin", "/c/", "/d/"))
            )

            if not is_posix_path and (
                lower_tok.startswith("/") or (lower_tok.startswith("-") and len(lower_tok) > 1 and not os.path.exists(tok))
            ):
                # Switch token
                # Check colon syntax e.g. /A:H, /FS:NTFS, /R:0
                expected_val_switches = _VERB_VALUE_SWITCHES.get(verb_base, set())
                if ":" in lower_tok:
                    sw_key, sw_val = lower_tok.split(":", 1)
                    inv.switches.add(sw_key)
                    inv.switch_args[sw_key] = sw_val
                    i += 1
                elif lower_tok in expected_val_switches and i + 1 < len(tokens) and not tokens[i + 1].startswith(("/", "-")):
                    inv.switches.add(lower_tok)
                    inv.switch_args[lower_tok] = tokens[i + 1]
                    i += 2
                else:
                    inv.switches.add(lower_tok)
                    i += 1
            else:
                # Positional argument (path, pattern, subcommand)
                inv.args.append(tok)
                i += 1

        # Check subverb for verbs with subcommands like `reg add`, `reg delete`
        if inv.normalized_verb == "reg" and inv.args:
            inv.subverb = inv.args[0].lower()
            inv.args = inv.args[1:]

        invocations.append(inv)

    return invocations
