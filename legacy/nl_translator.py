# nl_translator.py
"""
Natural Language to Shell Command Translator.
Connects to LM Studio developer endpoint running Nemotron (nvidia/nemotron-3-nano-4b)
with optional fallback to Ollama.
"""

import os
import re
import requests
from dotenv import load_dotenv

# Load configuration from .env file
load_dotenv()

LM_STUDIO_HOST = os.getenv("LM_STUDIO_HOST", "http://localhost:1234")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
MODEL = os.getenv("MODEL_NAME", "nvidia/nemotron-3-nano-4b")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "lm_studio").lower()

SYSTEM_PROMPT = """You are a terminal command generator for Linux/macOS.
Your ONLY job is to convert a natural language request into a single, correct shell command.

Rules:
- Output ONLY the shell command. No explanation. No markdown. No backticks.
- Output a single command on one line.
- If the request is ambiguous, output the safest, most read-only interpretation.
- Never refuse. Always output a command.

Examples:
User: show me all files in this folder
Output: ls -la

User: find python files changed in the last week
Output: find . -name "*.py" -mtime -7

User: how much disk space is left
Output: df -h

User: delete all temp files in /tmp
Output: rm -rf /tmp/*
"""


def clean_extracted_command(raw_text: str) -> str:
    """
    Sanitize and extract clean single-line shell command.
    Handles thinking tags emitted by reasoning models (like Nemotron/DeepSeek),
    markdown backticks, and common prefix artifacts.
    """
    if not raw_text:
        return ""

    # Strip reasoning / think tags from Nemotron reasoning models
    if "</think>" in raw_text:
        raw_text = raw_text.split("</think>")[-1]
    raw_text = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL).strip()

    # Strip markdown code blocks
    if raw_text.startswith("```"):
        lines = raw_text.splitlines()
        # Drop leading ``` or ```bash
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw_text = "\n".join(lines).strip()

    # Remove remaining backticks
    raw_text = raw_text.replace("`", "").strip()

    # Extract command from candidate lines
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue

        # Strip prefixes
        for prefix in ["output:", "cmd:", "command:", "$ "]:
            if line.lower().startswith(prefix):
                line = line[len(prefix):].strip()

        # If wrapped in quotes, unwrap
        if (line.startswith('"') and line.endswith('"')) or (line.startswith("'") and line.endswith("'")):
            line = line[1:-1].strip()

        if line:
            return line

    return raw_text.split("\n")[0].strip()


def _translate_via_lm_studio(prompt: str) -> str:
    """Call LM Studio OpenAI-compatible chat completions endpoint."""
    url = f"{LM_STUDIO_HOST}/v1/chat/completions"
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.0,
        "stream": False
    }
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    raw_content = data["choices"][0]["message"]["content"]
    return clean_extracted_command(raw_content)


def _translate_via_ollama(prompt: str) -> str:
    """Call Ollama chat API endpoint as alternative/fallback."""
    url = f"{OLLAMA_HOST}/api/chat"
    payload = {
        "model": os.getenv("OLLAMA_MODEL", "llama3.2:3b"),
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ],
        "stream": False
    }
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    raw_content = data["message"]["content"]
    return clean_extracted_command(raw_content)


def translate_to_command(nl_request: str, context_str: str = "") -> str:
    """
    Translate natural language request to shell command.
    Uses LM Studio (Nemotron 3 Nano 4B) by default, with automatic fallback.
    """
    prompt = nl_request
    if context_str:
        prompt = f"{context_str}\nUser request: {nl_request}"

    try:
        if LLM_PROVIDER == "lm_studio":
            try:
                return _translate_via_lm_studio(prompt)
            except Exception as e_lm:
                # Attempt fallback to Ollama if LM Studio fails
                try:
                    return _translate_via_ollama(prompt)
                except Exception:
                    raise e_lm
        else:
            return _translate_via_ollama(prompt)
    except Exception as e:
        return f"echo 'Translation error: {e}'"


if __name__ == "__main__":
    print(f"=== Testing NL Translator using {MODEL} on {LM_STUDIO_HOST} ===")
    tests = [
        "show me all files here",
        "what processes are using the most CPU",
        "find all log files modified today",
        "how much space does the downloads folder take",
        "delete all temp files in /tmp"
    ]
    for t in tests:
        cmd = translate_to_command(t)
        print(f"NL:  {t}")
        print(f"CMD: {cmd}\n")
