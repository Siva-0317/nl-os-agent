# tests/test_translator.py
"""
Unit tests for Natural Language Translation via Nemotron (LM Studio).
"""

import sys
import os

# Add parent directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from nl_translator import translate_to_command, clean_extracted_command


class TestCommandSanitization:
    def test_strip_thinking_tags(self):
        raw = "Let's think. We need to run ls -la to list files.\n</think>\nls -la"
        cleaned = clean_extracted_command(raw)
        assert cleaned == "ls -la"

    def test_strip_markdown_code_blocks(self):
        raw = "```bash\nfind . -name '*.py'\n```"
        cleaned = clean_extracted_command(raw)
        assert cleaned == "find . -name '*.py'"

    def test_strip_prefixes(self):
        assert clean_extracted_command("Output: df -h") == "df -h"
        assert clean_extracted_command("Command: ps aux") == "ps aux"
        assert clean_extracted_command("$ mkdir backup") == "mkdir backup"

    def test_strip_quotes(self):
        assert clean_extracted_command('"ls -la"') == "ls -la"
        assert clean_extracted_command("'pwd'") == "pwd"


class TestLiveTranslation:
    def test_list_files(self):
        cmd = translate_to_command("show me all files here")
        assert "ls" in cmd

    def test_disk_space(self):
        cmd = translate_to_command("how much disk space is left")
        assert "df" in cmd

    def test_find_python_files(self):
        cmd = translate_to_command("find all python files")
        assert "find" in cmd or "*.py" in cmd

    def test_no_explanation_in_output(self):
        cmd = translate_to_command("list files")
        assert len(cmd.splitlines()) == 1
        # Command should not start with common conversational filler
        first_word = cmd.lower().split()[0]
        assert first_word not in ["to", "you", "sure", "here", "i", "the", "this"]
