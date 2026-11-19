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
from nlagent.context.grounding import ground_request
from nlagent.context.layout import build_prompt
from nlagent.context.profile import UserProfile
from nlagent.context.state import SessionState
from nlagent.safety.effects import Effect, analyse, preview, tier_from_effect

DEFAULT_L0_SYSTEM_PROMPT = (
    "You are an AI-native operating system shell agent.\n"
    "Convert user natural language requests into exact, safe terminal commands matching the active OS, Shell, and CWD.\n"
    "- Strictly obey the Shell field in environment context: on 'powershell', emit valid PowerShell cmdlets ('Get-ChildItem -Hidden', 'Get-ChildItem -Force', 'New-Item -ItemType Directory', 'Set-Content', 'Rename-Item') with Windows paths ('C:\\\\Windows\\\\System32', 'C:\\\\ProgramData', 'C:\\\\') and never mix bash flags; on 'bash' or 'zsh', always emit POSIX commands ('ls -la /c/Windows/System32', 'find', 'grep') and never mix PowerShell or cmd commands like 'findstr'.\n"
    "- Avoid '-Recurse' (or deep recursive scans) on large directories or drive roots unless the user explicitly asks to search subdirectories/recursively.\n"
    "- Prefer paths relative to CWD for local workspace files unless an explicit path or external folder is shown in FILES.\n"
    "- If a destructive/ambiguous request lacks a clear target, set 'command' to null and ask a brief 'clarifying_question'.\n"
    "Output ONLY a single valid JSON object matching this schema:\n"
    "{\n"
    '  "command": "<exact shell command string or null>",\n'
    '  "targets": ["<list>", "<of>", "<targets>"],\n'
    '  "clarifying_question": "<short question if ambiguous, otherwise null>",\n'
    '  "explanation": "<short rationale>"\n'
    "}\n"
    "Do not include markdown fences, comments, or any extra text outside the JSON."
)


def get_default_env(cwd: str | None = None, shell: str | None = None) -> dict[str, Any]:
    """Inspect the local environment and build a deterministic L1 dictionary."""
    os_name = platform.system()
    shell_pref = (shell or os.environ.get("NLAGENT_SHELL", "")).strip().lower()
    if os_name == "Darwin":
        clean_os = f"macOS {platform.mac_ver()[0]}"
        default_shell = shell_pref or os.environ.get("SHELL", "zsh").split("/")[-1]
        path_style = "posix"
    elif os_name == "Linux":
        clean_os = "Linux (Ubuntu/Debian)"
        default_shell = shell_pref or os.environ.get("SHELL", "bash").split("/")[-1]
        path_style = "posix"
    else:
        clean_os = "Windows 11"
        if shell_pref == "bash" and shutil.which("bash"):
            default_shell = "bash"
        elif shell_pref == "cmd":
            default_shell = "cmd"
        else:
            default_shell = "powershell"
        path_style = "posix" if default_shell == "bash" else "windows"

    user = getpass.getuser() or "user"

    if default_shell in ("powershell", "cmd"):
        candidate_tools = [
            "cmd", "curl", "findstr", "git", "pip", "powershell", "python", "robocopy", "tar",
        ]
        available_tools = sorted([t for t in candidate_tools if shutil.which(t) is not None])
        if not available_tools:
            available_tools = ["cmd", "powershell", "python"]
    else:
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

    # Strip <think>...</think> reasoning blocks emitted by models like Nemotron / DeepSeek
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
    if "</think>" in text:
        text = text.split("</think>")[-1].strip()

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
        # Heal unescaped single backslashes inside Windows paths / PowerShell regexes (e.g. '^\.' or 'C:\Users')
        repaired = re.sub(r'(?<!\\)\\(?![\\/"bfnrtu])', r"\\\\", text)
        try:
            parsed = json.loads(repaired)
        except json.JSONDecodeError:
            raise ValueError(
                f"Failed to parse model output as JSON: {exc}\nRaw output: {raw_text[:200]}"
            ) from exc

    if not isinstance(parsed, dict):
        raise ValueError(
            f"Model proposal must be a JSON object, got {type(parsed).__name__}."
        )

    clarifying_q = parsed.get("clarifying_question") or parsed.get("question")
    needs_clarification = parsed.get("needs_clarification", False) or bool(clarifying_q)

    if "command" not in parsed:
        if clarifying_q or needs_clarification:
            command_val = ""
        else:
            raise ValueError(f"Model proposal missing required 'command' field: {parsed}")
    else:
        command_val = parsed["command"]

    if not isinstance(command_val, str):
        command_val = str(command_val) if command_val is not None else ""

    if not command_val.strip():
        explanation_val = str(
            parsed.get("explanation")
            or parsed.get("reason")
            or parsed.get("message")
            or parsed.get("refusal")
            or ""
        ).strip()
        if explanation_val and not (clarifying_q or needs_clarification):
            needs_clarification = True
            clarifying_q = explanation_val
        elif not (clarifying_q or needs_clarification):
            raise ValueError(f"'command' field must be a non-empty string, got: {command_val!r}")

    targets = parsed.get("targets", [])
    if isinstance(targets, str):
        targets = [targets]
    elif not isinstance(targets, list):
        targets = []

    res: dict[str, Any] = {
        "command": command_val.strip(),
        "targets": [str(t) for t in targets],
        "explanation": str(parsed.get("explanation", "")).strip(),
    }
    if clarifying_q:
        res["clarifying_question"] = str(clarifying_q).strip()
        res["needs_clarification"] = True
    elif needs_clarification:
        res["needs_clarification"] = True

    return res


def propose(
    request: str,
    session_state: SessionState,
    backend: Any | None = None,
    verified_cache: VerifiedCommandCache | None = None,
    env: dict[str, Any] | None = None,
    system_prompt: str | None = None,
    temperature: float = 0.0,
    n_predict: int = 256,
    reasoning: bool = True,
    config: Any | None = None,
    profile: UserProfile | None = None,
    enable_grounding: bool = True,
    grounding_k: int = 5,
) -> dict[str, Any]:
    """
    Safe command proposal pipeline.

    Flow:
    1. Resolve references and render compact SessionState (L2).
    2. Optional FILES block via bounded read-only grounding (L2, keeping L0/L1 byte-identical).
    3. Assemble 3-layer prompt via layout.build_prompt (L0, L1, L2).
    4. Check VerifiedCommandCache:
       - On hit: use verified proposal, bypass backend call.
       - On miss: query backend and validate JSON structure.
    5. Run static effect analysis (effects.analyse) on the proposed command.
    6. Determine risk tier via tier_from_effect.
    7. Return {command, tier, preview, ...}.

    NOTE: The LLM only proposes commands; NO execution occurs in this function.
    """
    if not request or not request.strip():
        raise ValueError("Cannot propose command for empty request.")

    # 1. Resolve referring expressions ("that file", "it") & render compact state
    resolved_request = session_state.resolve_reference(request.strip())
    rendered_state = session_state.render()

    # Grounding integration: discover real local filesystem paths matching request
    active_enable_grounding = getattr(config, "enable_grounding", enable_grounding) if config is not None else enable_grounding
    active_grounding_k = getattr(config, "grounding_k", grounding_k) if config is not None else grounding_k
    active_profile = getattr(config, "profile", profile) if config is not None else profile

    files_block = ""
    if active_enable_grounding:
        try:
            files_block = ground_request(
                request=resolved_request,
                cwd=session_state.cwd,
                profile=active_profile,
                k=active_grounding_k,
            )
        except Exception:
            files_block = ""

    # Optional FILES block after STATE block
    if files_block:
        turn_state = f"{rendered_state}\n\n{files_block}" if rendered_state else files_block
    else:
        turn_state = rendered_state

    # Resolve configuration overrides if provided
    active_reasoning = getattr(config, "reasoning", reasoning) if config is not None else reasoning
    active_temperature = getattr(config, "temperature", temperature) if config is not None else temperature
    active_n_predict = getattr(config, "n_predict", n_predict) if config is not None else n_predict

    # 2. Build deterministic L1 and 3-layer prompt (L0 and L1 remain strictly byte-identical)
    active_env = env if env is not None else get_default_env(cwd=session_state.cwd)
    l0 = system_prompt if system_prompt is not None else DEFAULT_L0_SYSTEM_PROMPT

    prompt_parts = build_prompt(
        l0=l0,
        l1=active_env,
        state=turn_state,
        request=resolved_request,
        reasoning=active_reasoning,
    )

    # 3. Check verified cache
    cached_proposal: dict[str, Any] | None = None
    is_cached = False
    clarifying_question: str | None = None
    needs_clarification: bool = False

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
            from nlagent.backends.llama_server import LlamaServerBackend, resolve_local_endpoint
            backend = LlamaServerBackend(endpoint_url=resolve_local_endpoint(), reasoning=active_reasoning)

        if hasattr(backend, "complete"):
            from nlagent.backends.llama_server import LlamaServerBackend
            complete_kwargs: dict[str, Any] = {
                "prompt": prompt_parts.full_text,
                "cache_prompt": True,
                "temperature": active_temperature,
                "n_predict": active_n_predict,
                "reasoning": active_reasoning,
            }
            if isinstance(backend, LlamaServerBackend):
                complete_kwargs["system_prompt"] = prompt_parts.prefix_text.strip()
            res = backend.complete(**complete_kwargs)
            if getattr(res, "status", "success") == "error" and getattr(res, "error", None):
                raise RuntimeError(f"Connection or backend error: {res.error}")
            raw_output = res.content if hasattr(res, "content") else str(res)
            if (
                (not raw_output or not raw_output.strip())
                and active_reasoning
                and getattr(res, "reasoning_tokens", 0) > 0
            ):
                # Thinking trace exhausted n_predict budget; retry cleanly with thinking disabled
                fallback_parts = build_prompt(
                    l0=l0,
                    l1=active_env,
                    state=turn_state,
                    request=resolved_request,
                    reasoning=False,
                )
                retry_kwargs: dict[str, Any] = {
                    "prompt": fallback_parts.full_text,
                    "cache_prompt": True,
                    "temperature": active_temperature,
                    "n_predict": active_n_predict,
                    "reasoning": False,
                }
                if isinstance(backend, LlamaServerBackend):
                    retry_kwargs["system_prompt"] = fallback_parts.prefix_text.strip()
                res_retry = backend.complete(**retry_kwargs)
                if getattr(res_retry, "status", "success") == "error" and getattr(res_retry, "error", None):
                    raise RuntimeError(f"Connection or backend error: {res_retry.error}")
                raw_output = res_retry.content if hasattr(res_retry, "content") else str(res_retry)
                prompt_parts = fallback_parts
        elif callable(backend):
            raw_output = str(backend(prompt_parts.full_text))
        else:
            raise TypeError(f"Backend does not support complete() and is not callable: {backend}")

        # JSON validation
        parsed = extract_json_proposal(raw_output)
        command = parsed["command"]
        targets = parsed["targets"]
        explanation = parsed["explanation"]
        clarifying_question = parsed.get("clarifying_question")
        needs_clarification = parsed.get("needs_clarification", False)

    # 4. Static effect analysis (mandatory for both fresh and cached proposals)
    if command:
        eff: Effect = analyse(command=command, cwd=session_state.cwd)
        tier = "ambiguous" if needs_clarification else tier_from_effect(eff)
        preview_text = preview(eff)
    else:
        eff = Effect()
        tier = "ambiguous"
        preview_text = (
            f"Clarification required: {clarifying_question}"
            if clarifying_question
            else "Ambiguous request: clarification required before execution."
        )

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
        "clarifying_question": clarifying_question,
        "needs_clarification": bool(clarifying_question or needs_clarification),
        "grounded_files": files_block,
        "prompt_parts": prompt_parts,
    }

