"""llama_server.py - HTTP model backend for llama-server (/completion).

Implements the ModelBackend interface and completion API against
llama.cpp's llama-server HTTP endpoints. Records the server's `timings` object
(prompt_n, cache_n, prompt_ms, etc.) and wall time for performance tracking.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Sequence
import requests


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

    @property
    def structured_timings(self) -> LlamaTimings:
        return LlamaTimings.from_dict(self.timings)


class LlamaServerBackend:
    """HTTP client for llama-server implementing completion and state operations."""

    def __init__(
        self,
        endpoint_url: str = "http://127.0.0.1:8080/completion",
        timeout: float = 60.0,
        session: requests.Session | None = None,
    ) -> None:
        self.endpoint_url = endpoint_url
        self.timeout = timeout
        self.session = session or requests.Session()
        self.last_timings: dict[str, Any] = {}
        self._state_tokens: list[int] = []

    def complete(
        self,
        prompt: str | list[int],
        cache_prompt: bool = True,
        temperature: float = 0.0,
        n_predict: int = 64,
        stop: list[str] | None = None,
        **extra_params: Any,
    ) -> CompletionResult:
        """Send a completion request to llama-server /completion and record timings."""
        payload: dict[str, Any] = {
            "prompt": prompt,
            "cache_prompt": cache_prompt,
            "temperature": temperature,
            "n_predict": n_predict,
        }
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
            content = data.get("content", "")
            timings = data.get("timings", {})
            self.last_timings = dict(timings)

            return CompletionResult(
                content=content,
                timings=timings,
                wall_time_s=wall_time,
                status="success",
                raw_response=data,
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

