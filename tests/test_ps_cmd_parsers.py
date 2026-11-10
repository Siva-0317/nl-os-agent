"""tests/test_ps_cmd_parsers.py - Unit tests for ps_parser and cmd_parser."""

from __future__ import annotations

import os
import sys
import pytest

_src = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _src not in sys.path:
    sys.path.insert(0, _src)

from nlagent.safety.cmd_parser import CmdInvocation, parse_cmd_line
from nlagent.safety.ps_parser import (
    ParsedPSCommand,
    PSParseResult,
    PersistentPowerShellParser,
    get_ps_parser,
    parse_powershell,
)


class TestPowerShellParser:
    """Direct tests for PersistentPowerShellParser and AST processing."""

    def test_parse_simple_cmdlet(self):
        res = parse_powershell("Remove-Item -Path test.txt -Force -Recurse")
        assert len(res.commands) >= 1
        cmd = res.commands[0]
        assert cmd.normalized_name == "Remove-Item"
        assert cmd.parameters.get("Path") == "test.txt"
        assert cmd.parameters.get("Force") is True
        assert cmd.parameters.get("Recurse") is True

    def test_parse_alias_resolution(self):
        res = parse_powershell("ri -Path data.log")
        assert len(res.commands) >= 1
        assert res.commands[0].normalized_name == "Remove-Item"

        res_mi = parse_powershell("mi src.txt dst.txt")
        assert res_mi.commands[0].normalized_name == "Move-Item"

        res_sc = parse_powershell("sc file.txt 'content'")
        assert res_sc.commands[0].normalized_name == "Set-Content"

    def test_parse_encoded_command_flags(self):
        res = parse_powershell("powershell.exe -EncodedCommand JAB4ID0gMQ==")
        assert res.has_encoded_command is True

        res_enc = parse_powershell("pwsh -enc JAB4ID0gMQ==")
        assert res_enc.has_encoded_command is True

    def test_parse_invoke_expression_flags(self):
        res = parse_powershell('Invoke-Expression "Get-Process"')
        assert res.has_invoke_expression is True

        res_iex = parse_powershell('iex "Stop-Process -Name evil"')
        assert res_iex.has_invoke_expression is True

    def test_parse_runas_elevation_flag(self):
        res = parse_powershell("Start-Process powershell.exe -Verb RunAs")
        assert res.has_runas is True

        res_colon = parse_powershell("Start-Process cmd.exe -Verb:RunAs")
        assert res_colon.has_runas is True

    def test_parse_syntax_error_resilience(self):
        # Unclosed quotes must not crash the parser
        res = parse_powershell('Remove-Item "unclosed quote')
        assert res.has_errors is True
        assert len(res.errors) >= 1

    def test_fallback_parser_direct(self):
        parser = PersistentPowerShellParser()
        # Direct test of pure-python fallback
        res = parser._fallback_parse("Remove-Item -Path secret.txt -Force; Start-Process powershell -Verb RunAs")
        assert len(res.commands) >= 2
        assert res.commands[0].normalized_name == "Remove-Item"
        assert res.has_runas is True


class TestCmdParser:
    """Direct tests for cmd.exe command line tokenizer and parser."""

    def test_parse_compound_operators(self):
        line = "mkdir build && cd build & echo hello > log.txt || echo failed"
        invs = parse_cmd_line(line)
        assert len(invs) == 4
        assert invs[0].normalized_verb == "mkdir"
        assert invs[1].normalized_verb == "cd"
        assert invs[2].normalized_verb == "echo"
        assert (">", "log.txt") in invs[2].redirections
        assert invs[3].normalized_verb == "echo"

    def test_parse_del_switches(self):
        line = 'del /f /s /q "C:\\Users\\Temp\\*.tmp"'
        invs = parse_cmd_line(line)
        assert len(invs) == 1
        inv = invs[0]
        assert inv.normalized_verb == "del"
        assert inv.is_recursive is True
        assert inv.is_force is True
        assert "C:\\Users\\Temp\\*.tmp" in inv.args

    def test_parse_robocopy_mir(self):
        line = 'robocopy "D:\\src" "E:\\backup" /MIR /R:0 /W:0'
        invs = parse_cmd_line(line)
        assert len(invs) == 1
        inv = invs[0]
        assert inv.normalized_verb == "robocopy"
        assert inv.is_mirror is True
        assert "D:\\src" in inv.args
        assert "E:\\backup" in inv.args

    def test_parse_taskkill_switches(self):
        line = "taskkill /F /IM notepad.exe"
        invs = parse_cmd_line(line)
        assert len(invs) == 1
        inv = invs[0]
        assert inv.normalized_verb == "taskkill"
        assert inv.switch_args.get("/im") == "notepad.exe"

    def test_parse_reg_subcommands(self):
        line = 'reg add "HKCU\\Software\\Test" /v "Val" /t REG_SZ /d "123" /f'
        invs = parse_cmd_line(line)
        assert len(invs) == 1
        inv = invs[0]
        assert inv.normalized_verb == "reg"
        assert inv.subverb == "add"
        assert "HKCU\\Software\\Test" in inv.args
        assert inv.switch_args.get("/v") == "Val"
        assert inv.switch_args.get("/d") == "123"

    def test_parse_hard_block_commands(self):
        line = "format D: /FS:NTFS /Q"
        invs = parse_cmd_line(line)
        assert len(invs) == 1
        assert invs[0].normalized_verb == "format"

        line_dp = "diskpart /s wipe.txt"
        invs_dp = parse_cmd_line(line_dp)
        assert len(invs_dp) == 1
        assert invs_dp[0].normalized_verb == "diskpart"

