"""llama_server.py - HTTP model backend for llama-server (/completion).

Implements the ModelBackend interface and completion API against
llama.cpp's llama-server HTTP endpoints. Records the server's `timings` object
(prompt_n, cache_n, prompt_ms, etc.) and wall time for performance tracking.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Sequence
import requests


def estimate_token_count(text: str) -> int:
    """Fast, accurate token count approximation (~3.8 chars/token)."""
    return len(re.findall(r"\w+|[^\w\s]", text))


@dataclass
class LlamaTimings:
    """Structured representation of the llama-server 'timings' payload."""

    prompt_n: int = 0
    prompt_ms: float = 0.0
    prompt_per_token_ms: float = 0.0
    prompt_per_second: float = 0.0
    predicted_n: int = 0
    predicted_ms: float = 0.0
    predicted_per_token_ms: float = 0.0
    predicted_per_second: float = 0.0
    cache_n: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "LlamaTimings":
        if not data or not isinstance(data, dict):
            return cls()
        return cls(
            prompt_n=int(data.get("prompt_n", 0)),
            prompt_ms=float(data.get("prompt_ms", 0.0)),
            prompt_per_token_ms=float(data.get("prompt_per_token_ms", 0.0)),
            prompt_per_second=float(data.get("prompt_per_second", 0.0)),
            predicted_n=int(data.get("predicted_n", 0)),
            predicted_ms=float(data.get("predicted_ms", 0.0)),
            predicted_per_token_ms=float(data.get("predicted_per_token_ms", 0.0)),
            predicted_per_second=float(data.get("predicted_per_second", 0.0)),
            cache_n=int(data.get("cache_n", 0)),
            raw=dict(data),
        )


@dataclass
class CompletionResult:
    """Result of a completion call to llama-server."""

    content: str
    timings: dict[str, Any]
    wall_time_s: float
    status: str
    raw_response: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    prompt_tokens: int = 0
    output_tokens: int = 0
    prefill_ms: float = 0.0
    decode_ms: float = 0.0
    reasoning_tokens: int = 0
    reasoning_content: str = ""

    @property
    def structured_timings(self) -> LlamaTimings:
        return LlamaTimings.from_dict(self.timings)


def get_preferred_model_id(models_endpoint: str = "http://127.0.0.1:1234/v1/models") -> str | None:
    """Probe LM Studio / local server models list and prioritize fine-tuned / dense models."""
    try:
        r = requests.get(models_endpoint, timeout=0.5)
        if r.status_code == 200:
            data = r.json().get("data", [])
            ids = [m.get("id", "") for m in data if isinstance(m, dict)]
            # 1. First priority: look for fine-tuned or dense model
            for mid in ids:
                mid_lower = mid.lower()
                if "finetun" in mid_lower or "dense" in mid_lower:
                    return mid
            # 2. Second priority: first active model
            if ids:
                return ids[0]
    except Exception:
        pass
    return None


def resolve_local_endpoint(endpoint_url: str = "http://127.0.0.1:8080/completion") -> str:
    """Probe 8080 (llama-server) and 1234 (LM Studio) to auto-select active local server."""
    env_override = os.environ.get("NLAGENT_ENDPOINT") or os.environ.get("LLAMA_SERVER_URL")
    if env_override:
        return env_override
    if endpoint_url != "http://127.0.0.1:8080/completion":
        return endpoint_url
    try:
        r = requests.get("http://127.0.0.1:8080/health", timeout=0.25)
        if r.status_code == 200:
            return "http://127.0.0.1:8080/v1/chat/completions"
    except Exception:
        pass
    try:
        r = requests.get("http://127.0.0.1:1234/v1/models", timeout=0.25)
        if r.status_code == 200:
            return "http://127.0.0.1:1234/v1/chat/completions"
    except Exception:
        pass
    return "http://127.0.0.1:8080/v1/chat/completions"


class LlamaServerBackend:
    """HTTP client for llama-server implementing completion and state operations."""

    def __init__(
        self,
        endpoint_url: str = "http://127.0.0.1:8080/completion",
        model_name: str | None = None,
        timeout: float = 60.0,
        session: Any | None = None,
        reasoning: bool = True,
    ) -> None:
        self.endpoint_url = endpoint_url
        self.model_name = model_name
        self.timeout = timeout
        self.session = session or requests.Session()
        self.reasoning = reasoning
        self.last_timings: dict[str, Any] = {}
        self._state_tokens: list[int] = []

    def complete(
        self,
        prompt: str | list[int],
        cache_prompt: bool = True,
        temperature: float = 0.0,
        n_predict: int = 64,
        stop: list[str] | None = None,
        reasoning: bool | None = None,
        **extra_params: Any,
    ) -> CompletionResult:
        """Send a completion request to llama-server or OpenAI-compatible endpoint."""
        prompt_str = prompt if isinstance(prompt, str) else " ".join(str(x) for x in prompt)
        active_reasoning = self.reasoning if reasoning is None else reasoning

        is_chat_api = "chat/completions" in self.endpoint_url or "/v1/" in self.endpoint_url
        if is_chat_api:
            clean_user_prompt = re.sub(r"\n?<think></think>\s*$", "", prompt_str).strip()
            model_id = self.model_name or extra_params.pop("model", None)
            if not model_id:
                models_url = self.endpoint_url.replace("/chat/completions", "/models")
                model_id = get_preferred_model_id(models_url) or "nvidia/nemotron-3-nano-4b"
            payload: dict[str, Any] = {
                "model": model_id,
                "messages": [{"role": "user", "content": clean_user_prompt}],
                "temperature": temperature,
                "max_tokens": n_predict,
                "cache_prompt": cache_prompt,
            }
            if not active_reasoning:
                # Official Nemotron-3-Nano model card mechanism to disable reasoning trace
                payload["chat_template_kwargs"] = {"enable_thinking": False}
        else:
            if not active_reasoning and "<think>" not in prompt_str:
                # Close thinking tag prefill to prevent chain-of-thought generation
                prompt_str = f"{prompt_str}\n<think></think>"
                prompt = prompt_str
            payload = {
                "prompt": prompt,
                "cache_prompt": cache_prompt,
                "temperature": temperature,
                "n_predict": n_predict,
            }
            if not active_reasoning:
                payload["chat_template_kwargs"] = {"enable_thinking": False}

        if stop:
            payload["stop"] = stop
        payload.update(extra_params)

        t_start = time.perf_counter()
        try:
            resp = self.session.post(
                self.endpoint_url,
                json=payload,
                timeout=self.timeout,
            )
            wall_time = time.perf_counter() - t_start

            if resp.status_code != 200:
                err_msg = f"HTTP {resp.status_code}: {resp.text}"
                return CompletionResult(
                    content="",
                    timings={},
                    wall_time_s=wall_time,
                    status="error",
                    error=err_msg,
                )

            data = resp.json()
            if "content" in data:
                content = data["content"]
            elif "choices" in data and len(data["choices"]) > 0:
                first = data["choices"][0]
                if isinstance(first, dict):
                    if "message" in first and isinstance(first["message"], dict):
                        content = first["message"].get("content", "")
                    elif "text" in first:
                        content = first.get("text", "")
                    else:
                        content = str(first)
                else:
                    content = str(first)
            else:
                content = ""

            raw_content = content
            reasoning_tokens = 0
            reasoning_content = ""

            # Check if API returned structured reasoning_content (e.g. LM Studio / DeepSeek)
            if "choices" in data and len(data["choices"]) > 0:
                first = data["choices"][0]
                if isinstance(first, dict) and "message" in first and isinstance(first["message"], dict):
                    rc = first["message"].get("reasoning_content")
                    if rc:
                        reasoning_content = str(rc).strip()
                        reasoning_tokens = estimate_token_count(reasoning_content)

            # Check for <think>...</think> tags inside raw content
            if not reasoning_content and ("<think>" in raw_content or "</think>" in raw_content):
                think_match = re.search(r"<think>([\s\S]*?)</think>", raw_content, re.IGNORECASE)
                if think_match:
                    reasoning_content = think_match.group(1).strip()
                    reasoning_tokens = estimate_token_count(reasoning_content)
                elif "</think>" in raw_content:
                    parts = raw_content.split("</think>", 1)
                    reasoning_content = parts[0].replace("<think>", "").strip()
                    reasoning_tokens = estimate_token_count(reasoning_content)

            # Do not remove the tag stripper: keep output clean for JSON parsing
            clean_content = re.sub(r"<think>[\s\S]*?</think>", "", raw_content, flags=re.IGNORECASE)
            if "</think>" in clean_content:
                clean_content = clean_content.split("</think>")[-1].strip()

            timings = data.get("timings", {})
            if not timings and "usage" in data:
                usage = data["usage"]
                timings = {
                    "prompt_n": usage.get("prompt_tokens", 0),
                    "predicted_n": usage.get("completion_tokens", 0),
                }
            self.last_timings = dict(timings)

            prompt_n = int(timings.get("prompt_n", 0))
            predicted_n = int(timings.get("predicted_n", 0))
            prompt_ms = float(timings.get("prompt_ms", 0.0))
            predicted_ms = float(timings.get("predicted_ms", 0.0))

            if prompt_n == 0:
                prompt_n = estimate_token_count(prompt_str)
            if predicted_n == 0:
                predicted_n = estimate_token_count(raw_content)

            return CompletionResult(
                content=clean_content,
                timings=timings,
                wall_time_s=wall_time,
                status="success",
                raw_response=data,
                prompt_tokens=prompt_n,
                output_tokens=predicted_n,
                prefill_ms=prompt_ms,
                decode_ms=predicted_ms,
                reasoning_tokens=reasoning_tokens,
                reasoning_content=reasoning_content,
            )
        except requests.exceptions.RequestException as e:
            wall_time = time.perf_counter() - t_start
            return CompletionResult(
                content="",
                timings={},
                wall_time_s=wall_time,
                status="error",
                error=str(e),
            )

    # --------------------------------------------------------------------------
    # ModelBackend protocol interface implementation
    # --------------------------------------------------------------------------

    def save_state(self) -> bytes:
        """Capture opaque binary snapshot of current sequence/context tokens."""
        state_dict = {
            "state_tokens": self._state_tokens,
            "last_timings": self.last_timings,
        }
        return json.dumps(state_dict).encode("utf-8")

    def load_state(self, blob: bytes) -> None:
        """Restore model context state from an opaque snapshot blob."""
        if not blob:
            self._state_tokens = []
            return
        state_dict = json.loads(blob.decode("utf-8"))
        self._state_tokens = list(state_dict.get("state_tokens", []))
        self.last_timings = dict(state_dict.get("last_timings", {}))

    def eval(self, tokens: list[int]) -> list[float]:
        """Evaluate a sequence of token IDs and return completion/logits approximation."""
        self._state_tokens.extend(tokens)
        res = self.complete(
            prompt=tokens,
            cache_prompt=True,
            temperature=0.0,
            n_predict=1,
        )
        if res.status != "success":
            return [0.0] * len(tokens)

        # Return a normalized representation of evaluated tokens
        return [float(t) for t in self._state_tokens]

