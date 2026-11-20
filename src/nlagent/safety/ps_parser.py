"""
ps_parser.py - Persistent PowerShell AST Parser.

Provides structured AST analysis of PowerShell commands using a persistent background
PowerShell helper process that runs `[System.Management.Automation.Language.Parser]`.
Falls back gracefully to a pure-Python tokenizer if PowerShell is unavailable.
"""

from __future__ import annotations

import atexit
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

PS_ALIASES: dict[str, str] = {
    "ri": "Remove-Item",
    "rm": "Remove-Item",
    "rmdir": "Remove-Item",
    "del": "Remove-Item",
    "erase": "Remove-Item",
    "rd": "Remove-Item",
    "mi": "Move-Item",
    "mv": "Move-Item",
    "move": "Move-Item",
    "cpi": "Copy-Item",
    "cp": "Copy-Item",
    "copy": "Copy-Item",
    "ci": "Copy-Item",
    "sc": "Set-Content",
    "clc": "Clear-Content",
    "gc": "Get-Content",
    "cat": "Get-Content",
    "type": "Get-Content",
    "spps": "Stop-Process",
    "kill": "Stop-Process",
    "iex": "Invoke-Expression",
    "icm": "Invoke-Command",
    "saps": "Start-Process",
    "start": "Start-Process",
    "ni": "New-Item",
    "md": "New-Item",
    "rni": "Rename-Item",
    "ren": "Rename-Item",
    "sp": "Set-ItemProperty",
    "rp": "Remove-ItemProperty",
    "clp": "Clear-ItemProperty",
    "gp": "Get-ItemProperty",
    "gps": "Get-Process",
    "ps": "Get-Process",
    "gci": "Get-ChildItem",
    "ls": "Get-ChildItem",
    "dir": "Get-ChildItem",
    "ac": "Add-Content",
}


# Common PowerShell and POSIX flag names that act as boolean switches (do not take values)
_KNOWN_SWITCH_PARAMS: set[str] = {
    "r",
    "f",
    "rf",
    "fr",
    "recurse",
    "force",
    "whatif",
    "confirm",
    "verbose",
    "debug",
    "s",
    "q",
    "y",
    "a",
    "mir",
    "purge",
    "e",
    "h",
    "v",
    "append",
}


@dataclass
class ParsedPSCommand:
    """Represents a single command invocation within a PowerShell script/pipeline."""

    name: str
    elements: list[str] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)
    positional: list[str] = field(default_factory=list)
    redirections: list[dict[str, Any]] = field(default_factory=list)

    @property
    def normalized_name(self) -> str:
        """Return canonical cmdlet name resolved through PowerShell alias table."""
        lower = self.name.lower()
        for alias, target in PS_ALIASES.items():
            if lower == alias.lower():
                return target
        return self.name


@dataclass
class PSParseResult:
    """Result of parsing a PowerShell script or command line."""

    commands: list[ParsedPSCommand] = field(default_factory=list)
    has_errors: bool = False
    errors: list[str] = field(default_factory=list)
    has_encoded_command: bool = False
    has_invoke_expression: bool = False
    has_runas: bool = False
    has_subexpression: bool = False
    raw_ast: dict[str, Any] = field(default_factory=dict)


# Background PowerShell Worker Script
_WORKER_SCRIPT = """
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$stream = [System.Console]::OpenStandardInput()
$reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8)

while ($true) {
    $line = $reader.ReadLine()
    if ($null -eq $line -or $line -eq "__EXIT__") { break }
    if ($line.Trim().Length -eq 0) { continue }
    try {
        $rawBytes = [System.Convert]::FromBase64String($line)
        $code = [System.Text.Encoding]::UTF8.GetString($rawBytes)
        $tokens = $null
        $errors = $null
        $ast = [System.Management.Automation.Language.Parser]::ParseInput($code, [ref]$tokens, [ref]$errors)

        $cmdNodes = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true)
        $cmds = @()
        foreach ($c in $cmdNodes) {
            $name = $c.GetCommandName()
            $elems = @()
            $redirs = @()
            foreach ($e in $c.CommandElements) {
                $elems += $e.Extent.Text
            }
            if ($c.Redirections) {
                foreach ($r in $c.Redirections) {
                    $redirs += @{
                        type = $r.GetType().Name
                        text = $r.Extent.Text
                    }
                }
            }
            $cmds += @{
                command = if ($name) { $name } else { "" }
                elements = $elems
                redirections = $redirs
            }
        }

        $errMsgs = @()
        if ($errors) {
            foreach ($err in $errors) { $errMsgs += $err.Message }
        }

        # Check subexpressions: $(...) or `@(...)`
        $subExprs = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.SubExpressionAst] }, $true)

        $res = @{
            status = "ok"
            commands = $cmds
            errors = $errMsgs
            has_subexpression = ($subExprs.Count -gt 0)
        }
        $out = ConvertTo-Json -InputObject $res -Compress -Depth 5
        [System.Console]::WriteLine($out)
        [System.Console]::Out.Flush()
    } catch {
        $errRes = @{
            status = "error"
            message = $_.Exception.Message
        }
        $out = ConvertTo-Json -InputObject $errRes -Compress
        [System.Console]::WriteLine($out)
        [System.Console]::Out.Flush()
    }
}
"""


class PersistentPowerShellParser:
    """Manages a long-running PowerShell worker process to extract AST JSON."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._available: Optional[bool] = None

    def _get_ps_executable(self) -> Optional[str]:
        for exe in ("powershell.exe", "pwsh.exe", "powershell", "pwsh"):
            found = shutil.which(exe)
            if found:
                return found
        return None

    def _start_process(self) -> bool:
        if self._proc is not None and self._proc.poll() is None:
            return True

        exe = self._get_ps_executable()
        if not exe:
            self._available = False
            return False

        try:
            cf = 0x08000000 if sys.platform == "win32" else 0
            # Inline worker script encoded into powershell command
            # Pass via -Command with streaming stdin
            self._proc = subprocess.Popen(
                [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _WORKER_SCRIPT],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
                creationflags=cf,
            )
            self._available = True
            return True
        except Exception:
            self._available = False
            self._proc = None
            return False

    def close(self) -> None:
        with self._lock:
            if self._proc is not None:
                try:
                    if self._proc.stdin and not self._proc.stdin.closed:
                        self._proc.stdin.write("__EXIT__\n")
                        self._proc.stdin.flush()
                    self._proc.terminate()
                    self._proc.wait(timeout=2)
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
                finally:
                    self._proc = None

    def parse(self, script: str) -> PSParseResult:
        """Parse a PowerShell command string into a PSParseResult."""
        with self._lock:
            if not self._start_process() or self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
                return self._fallback_parse(script)

            try:
                b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")
                self._proc.stdin.write(b64 + "\n")
                self._proc.stdin.flush()
                resp_line = self._proc.stdout.readline()
                if not resp_line:
                    # Process died, restart once and fallback if needed
                    self.close()
                    if self._start_process() and self._proc and self._proc.stdin and self._proc.stdout:
                        self._proc.stdin.write(b64 + "\n")
                        self._proc.stdin.flush()
                        resp_line = self._proc.stdout.readline()

                if not resp_line:
                    return self._fallback_parse(script)

                data = json.loads(resp_line.strip())
                if data.get("status") != "ok":
                    return self._fallback_parse(script)

                return self._build_result(script, data)
            except Exception:
                return self._fallback_parse(script)

    def _build_result(self, script: str, data: dict[str, Any]) -> PSParseResult:
        result = PSParseResult(
            has_errors=bool(data.get("errors")),
            errors=data.get("errors", []),
            has_subexpression=data.get("has_subexpression", False),
            raw_ast=data,
        )

        # Global flag inspection on raw script
        lower_script = script.lower()
        if any(f in lower_script for f in ("-encodedcommand", "-encoded", "-enc ", " -e ")):
            result.has_encoded_command = True
        if "$(" in script or "`" in script:
            result.has_subexpression = True

        for cmd_dict in data.get("commands", []):
            cmd_name = cmd_dict.get("command", "")
            raw_elems = cmd_dict.get("elements", [])
            redirs = cmd_dict.get("redirections", [])

            parsed_cmd = self._parse_command_elements(cmd_name, raw_elems, redirs)
            result.commands.append(parsed_cmd)

            # Check special flags on command
            norm = parsed_cmd.normalized_name.lower()
            if norm == "invoke-expression":
                result.has_invoke_expression = True
            if norm == "start-process":
                verb = parsed_cmd.parameters.get("Verb", "")
                if isinstance(verb, str) and verb.lower() == "runas":
                    result.has_runas = True

            for p_name in parsed_cmd.parameters:
                p_lower = p_name.lower()
                if p_lower in ("encodedcommand", "enc", "e"):
                    result.has_encoded_command = True

        return result

    def _parse_command_elements(
        self, cmd_name: str, elements: list[str], redirections: list[dict[str, Any]]
    ) -> ParsedPSCommand:
        """Parse raw element tokens into named parameters, positional arguments, and flags."""
        cmd = ParsedPSCommand(name=cmd_name, elements=elements, redirections=redirections)
        if not elements:
            return cmd

        # First element is usually the command name itself
        tokens = list(elements)
        if tokens and (tokens[0].lower() == cmd_name.lower() or not cmd_name):
            if not cmd_name:
                cmd.name = tokens[0]
            tokens = tokens[1:]

        i = 0
        while i < len(tokens):
            tok = tokens[i]
            # Handle cmd.exe style switches like /Y, /S, /F, /Q, /MIR
            if tok.startswith("/") and len(tok) > 1 and not (
                tok.count("/") > 1 or any(tok.lower().startswith(p) for p in ("/etc", "/usr", "/bin", "/var", "/c/", "/d/"))
            ):
                sw = tok[1:]
                if ":" in sw:
                    k, v = sw.split(":", 1)
                    cmd.parameters[k.lower()] = self._strip_quotes(v)
                else:
                    cmd.parameters[sw.lower()] = True
                i += 1
            elif tok.startswith("-") and len(tok) > 1 and not tok.startswith("--"):
                # Parameter name e.g. -Path, -Force, -Verb:RunAs, -rf
                param_spec = tok[1:]
                if ":" in param_spec:
                    p_name, p_val = param_spec.split(":", 1)
                    cmd.parameters[p_name] = self._strip_quotes(p_val)
                    i += 1
                elif param_spec.lower() in _KNOWN_SWITCH_PARAMS:
                    cmd.parameters[param_spec] = True
                    i += 1
                else:
                    p_name = param_spec
                    # Check if next token is a value (not another parameter)
                    if i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                        val = self._strip_quotes(tokens[i + 1])
                        cmd.parameters[p_name] = val
                        i += 2
                    else:
                        # Switch parameter (e.g. -Force, -Recurse)
                        cmd.parameters[p_name] = True
                        i += 1
            elif tok.startswith("--"):
                # Long flag
                p_name = tok[2:]
                if i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                    cmd.parameters[p_name] = self._strip_quotes(tokens[i + 1])
                    i += 2
                else:
                    cmd.parameters[p_name] = True
                    i += 1
            else:
                # Positional argument
                cmd.positional.append(self._strip_quotes(tok))
                i += 1

        return cmd

    @staticmethod
    def _strip_quotes(s: str) -> str:
        s = s.strip()
        if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
            return s[1:-1]
        return s

    def _fallback_parse(self, script: str) -> PSParseResult:
        """Pure-Python fallback when powershell.exe is unavailable."""
        result = PSParseResult()
        lower_script = script.lower()
        if any(f in lower_script for f in ("-encodedcommand", "-encoded", "-enc ", " -e ")):
            result.has_encoded_command = True
        if "$(" in script or "`" in script:
            result.has_subexpression = True

        # Split on ; and |
        segments = [s.strip() for s in re.split(r"[;|]", script) if s.strip()]
        for seg in segments:
            # Tokenize respecting double and single quotes
            tokens = [m.group(0) for m in re.finditer(r'[^\s"\']+|"[^"]*"|\'[^\']*\'', seg)]
            if not tokens:
                continue
            cmd_name = tokens[0]
            parsed_cmd = self._parse_command_elements(cmd_name, tokens, [])
            result.commands.append(parsed_cmd)

            norm = parsed_cmd.normalized_name.lower()
            if norm == "invoke-expression":
                result.has_invoke_expression = True
            if norm == "start-process":
                verb = parsed_cmd.parameters.get("Verb", "")
                if isinstance(verb, str) and verb.lower() == "runas":
                    result.has_runas = True
            for p_name in parsed_cmd.parameters:
                if p_name.lower() in ("encodedcommand", "enc", "e"):
                    result.has_encoded_command = True

        return result


# Global Singleton Instance
_parser_singleton: Optional[PersistentPowerShellParser] = None
_singleton_lock = threading.Lock()


def get_ps_parser() -> PersistentPowerShellParser:
    """Return the global PersistentPowerShellParser singleton."""
    global _parser_singleton
    with _singleton_lock:
        if _parser_singleton is None:
            _parser_singleton = PersistentPowerShellParser()
    return _parser_singleton


def parse_powershell(script: str) -> PSParseResult:
    """Parse a PowerShell command line using the persistent AST helper."""
    return get_ps_parser().parse(script)


def shutdown_ps_worker() -> None:
    """Shut down the persistent PowerShell background process."""
    global _parser_singleton
    with _singleton_lock:
        if _parser_singleton is not None:
            _parser_singleton.close()
            _parser_singleton = None


atexit.register(shutdown_ps_worker)
