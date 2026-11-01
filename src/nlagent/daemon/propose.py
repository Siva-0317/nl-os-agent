"""
propose.py - Safe LLM Command Proposal Pipeline.

Follows the pipeline:
request -> SessionState.render -> layout.build_prompt
-> (verified_cache lookup) -> backend -> JSON validation
-> effects.analyse -> tier_from_effect -> return {command, tier, preview}.

The LLM only proposes commands; no execution occurs in this module.
"""

from __future__ import annotations

import getpass
import json
import os
import platform
import re
import shutil
from typing import Any, Callable

from nlagent.cache.verified_cache import VerifiedCommandCache, make_env_key
from nlagent.context.layout import build_prompt
from nlagent.context.state import SessionState
from nlagent.safety.effects import Effect, analyse, preview, tier_from_effect

DEFAULT_L0_SYSTEM_PROMPT = (
    "You are an AI-native operating system shell agent for Linux and macOS.\n"
    "Convert user natural language requests into exact, safe terminal commands.\n"
    "You MUST respond ONLY with a single valid JSON object matching this schema:\n"
    "{\n"
    '  "command": "<exact shell command string>",\n'
    '  "targets": ["<list>", "<of>", "<targets>"],\n'
    '  "explanation": "<short rationale>"\n'
    "}\n"
    "Do not include markdown fences, comments, or any extra text outside the JSON."
)


def get_default_env(cwd: str | None = None) -> dict[str, Any]:
    """Inspect the local environment and build a deterministic L1 dictionary."""
    os_name = platform.system()
    if os_name == "Darwin":
        clean_os = f"macOS {platform.mac_ver()[0]}"
        default_shell = os.environ.get("SHELL", "zsh").split("/")[-1]
        path_style = "posix"
    elif os_name == "Linux":
        clean_os = "Linux (Ubuntu/Debian)"
        default_shell = os.environ.get("SHELL", "bash").split("/")[-1]
        path_style = "posix"
    else:
        clean_os = "Windows 11"
        default_shell = "bash" if shutil.which("bash") else "powershell"
        path_style = "posix" if shutil.which("bash") else "windows"

    user = getpass.getuser() or "user"

    candidate_tools = [
        "awk", "bash", "cat", "curl", "docker", "find", "git",
        "grep", "pip", "python3", "sed", "tar", "zsh",
    ]
    available_tools = sorted([t for t in candidate_tools if shutil.which(t) is not None])
    if not available_tools:
        available_tools = ["bash", "cat", "curl", "git", "grep", "python3"]

    return {
        "available_tools": available_tools,
        "os": clean_os,
        "path_style": path_style,
        "shell": default_shell,
        "user": user,
    }


def extract_json_proposal(raw_text: str) -> dict[str, Any]:
    """
    Extract and validate structured proposal JSON from raw model output.

    Handles markdown fences (```json ... ```), surrounding prose,
    and validates required keys ('command').
    """
    if not raw_text or not raw_text.strip():
        raise ValueError("Backend returned empty response; cannot extract proposal.")

    text = raw_text.strip()

    # Remove markdown code fences if present
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if fence_match:
        text = fence_match.group(1).strip()

    # If text is not direct JSON, locate the outermost balanced braces { ... }
    if not (text.startswith("{") and text.endswith("}")):
        start_idx = text.find("{")
        end_idx = text.rfind("}")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            text = text[start_idx : end_idx + 1]

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Failed to parse model output as JSON: {exc}\nRaw output: {raw_text[:200]}"
        ) from exc

    if not isinstance(parsed, dict):
        raise ValueError(
            f"Model proposal must be a JSON object, got {type(parsed).__name__}."
        )

    if "command" not in parsed:
        raise ValueError(f"Model proposal missing required 'command' field: {parsed}")

    command_val = parsed["command"]
    if not isinstance(command_val, str) or not command_val.strip():
        raise ValueError(f"'command' field must be a non-empty string, got: {command_val!r}")

    targets = parsed.get("targets", [])
    if isinstance(targets, str):
        targets = [targets]
    elif not isinstance(targets, list):
        targets = []

    return {
        "command": command_val.strip(),
        "targets": [str(t) for t in targets],
        "explanation": str(parsed.get("explanation", "")).strip(),
    }


def propose(
    request: str,
    session_state: SessionState,
    backend: Any | None = None,
    verified_cache: VerifiedCommandCache | None = None,
    env: dict[str, Any] | None = None,
    system_prompt: str | None = None,
    temperature: float = 0.0,
    n_predict: int = 128,
) -> dict[str, Any]:
    """
    Safe command proposal pipeline.

    Flow:
    1. Resolve references and render compact SessionState (L2).
    2. Assemble 3-layer prompt via layout.build_prompt (L0, L1, L2).
    3. Check VerifiedCommandCache:
       - On hit: use verified proposal, bypass backend call.
       - On miss: query backend and validate JSON structure.
    4. Run static effect analysis (effects.analyse) on the proposed command.
    5. Determine risk tier via tier_from_effect.
    6. Return {command, tier, preview, ...}.

    NOTE: The LLM only proposes commands; NO execution occurs in this function.
    """
    if not request or not request.strip():
        raise ValueError("Cannot propose command for empty request.")

    # 1. Resolve referring expressions ("that file", "it") & render compact state
    resolved_request = session_state.resolve_reference(request.strip())
    rendered_state = session_state.render()

    # 2. Build deterministic L1 and 3-layer prompt
    active_env = env if env is not None else get_default_env(cwd=session_state.cwd)
    l0 = system_prompt if system_prompt is not None else DEFAULT_L0_SYSTEM_PROMPT

    prompt_parts = build_prompt(
        l0=l0,
        l1=active_env,
        state=rendered_state,
        request=resolved_request,
    )

    # 3. Check verified cache
    cached_proposal: dict[str, Any] | None = None
    is_cached = False

    if verified_cache is not None:
        env_key = make_env_key(
            os_name=str(active_env.get("os", "")),
            shell_name=str(active_env.get("shell", "")),
            cwd=session_state.cwd,
        )
        cached_proposal = verified_cache.lookup(request=resolved_request, env_key=env_key)

    if cached_proposal is not None:
        command = str(cached_proposal.get("command", "")).strip()
        targets = list(cached_proposal.get("targets", []))
        explanation = str(cached_proposal.get("explanation", "Retrieved from verified cache"))
        is_cached = True
        raw_output = None
    else:
        # Backend execution
        if backend is None:
            from nlagent.backends.llama_server import LlamaServerBackend
            backend = LlamaServerBackend()

        if hasattr(backend, "complete"):
            res = backend.complete(
                prompt=prompt_parts.full_text,
                cache_prompt=True,
                temperature=temperature,
                n_predict=n_predict,
            )
            raw_output = res.content if hasattr(res, "content") else str(res)
        elif callable(backend):
            raw_output = str(backend(prompt_parts.full_text))
        else:
            raise TypeError(f"Backend does not support complete() and is not callable: {backend}")

        # JSON validation
        parsed = extract_json_proposal(raw_output)
        command = parsed["command"]
        targets = parsed["targets"]
        explanation = parsed["explanation"]

    # 4. Static effect analysis (mandatory for both fresh and cached proposals)
    eff: Effect = analyse(command=command, cwd=session_state.cwd)

    # 5. Risk tier and preview
    tier = tier_from_effect(eff)
    preview_text = preview(eff)

    return {
        "command": command,
        "tier": tier,
        "preview": preview_text,
        "targets": targets,
        "explanation": explanation,
        "cached": is_cached,
        "effect": eff,
        "request": resolved_request,
        "raw_output": raw_output,
    }

