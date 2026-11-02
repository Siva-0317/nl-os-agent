"""
config.py - Global and Per-Session Agent Configuration.

Encapsulates runtime parameters:
- reasoning: bool (controls thinking trace emission in Nemotron-3-Nano)
- model_name: str
- endpoint_url: str
- timeout: float
- temperature: float
- n_predict: int
- cache_prompt: bool
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class AgentConfig:
    """Agent runtime configuration with reasoning control."""

    reasoning: bool = True
    model_name: str = "nvidia/nemotron-3-nano-4b"
    endpoint_url: str = "http://127.0.0.1:8080/completion"
    timeout: float = 60.0
    temperature: float = 0.0
    n_predict: int = 64
    cache_prompt: bool = True

    @classmethod
    def from_env(cls) -> "AgentConfig":
        """Instantiate AgentConfig with environment variable overrides."""
        reasoning_env = os.environ.get("NLAGENT_REASONING", "").strip().lower()
        reasoning = True
        if reasoning_env in ("0", "false", "off", "no"):
            reasoning = False

        endpoint_url = os.environ.get(
            "NLAGENT_ENDPOINT",
            os.environ.get("LLAMA_SERVER_URL", "http://127.0.0.1:8080/completion"),
        )
        model_name = os.environ.get("NLAGENT_MODEL", "nvidia/nemotron-3-nano-4b")
        timeout = float(os.environ.get("NLAGENT_TIMEOUT", "60.0"))
        temperature = float(os.environ.get("NLAGENT_TEMPERATURE", "0.0"))
        n_predict = int(os.environ.get("NLAGENT_N_PREDICT", "64"))
        cache_prompt = os.environ.get("NLAGENT_CACHE_PROMPT", "1").lower() not in ("0", "false", "no")

        return cls(
            reasoning=reasoning,
            model_name=model_name,
            endpoint_url=endpoint_url,
            timeout=timeout,
            temperature=temperature,
            n_predict=n_predict,
            cache_prompt=cache_prompt,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentConfig":
        """Build AgentConfig from dictionary, ignoring unrecognized keys."""
        valid_keys = set(cls.__dataclass_fields__.keys())
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)

    def to_dict(self) -> dict[str, Any]:
        """Convert configuration to dictionary."""
        return asdict(self)

