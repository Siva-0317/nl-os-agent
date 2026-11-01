"""
harness.py - Latency and prefix cache measurement harness for llama-server.

Follows Task T3 and Sections 4 & 5.4 of Research_Plan_Context_and_Latency.md:
- Sends requests to llama-server /completion endpoint with cache_prompt on/off.
- Records wall time and server timings (prompt_n, cache_n, prompt_ms, etc.).
- Appends structured records to results/*.jsonl.
- Supports running N interleaved sessions in round-robin turn order.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
import requests


@dataclass
class BenchmarkTurn:
    """A single turn within a session benchmark script."""
    prompt: str
    n_predict: int = 64
    temperature: float = 0.0


@dataclass
class SessionScript:
    """A sequence of turns belonging to one benchmark session."""
    session_id: str
    turns: list[BenchmarkTurn] = field(default_factory=list)


@dataclass
class BenchmarkRecord:
    """Structured record written to JSONL for each query."""
    request_id: str
    session_id: str
    turn_index: int
    prompt: str
    cache_prompt: bool
    wall_time_s: float
    timings: dict[str, Any]
    response_text: str
    status: str
    timestamp: str
    experiment_id: str = ""
    error: str | None = None


def generate_interleaving_order(
    sessions: list[SessionScript],
) -> list[tuple[str, int, BenchmarkTurn]]:
    """
    Generate round-robin interleaving order across N sessions.

    Orders turns as:
    Turn 0 of Session 0, Turn 0 of Session 1, ..., Turn 0 of Session N-1,
    Turn 1 of Session 0, Turn 1 of Session 1, ...,
    until all turns from all sessions are scheduled.
    """
    if not sessions:
        return []

    max_turns = max(len(s.turns) for s in sessions)
    order: list[tuple[str, int, BenchmarkTurn]] = []

    for turn_idx in range(max_turns):
        for sess in sessions:
            if turn_idx < len(sess.turns):
                order.append((sess.session_id, turn_idx, sess.turns[turn_idx]))

    return order


class BenchmarkHarness:
    """
    Benchmark harness that executes queries against llama-server
    and persists latency and cache timings to JSONL.
    """

    def __init__(
        self,
        endpoint_url: str = "http://127.0.0.1:8080/completion",
        results_dir: str = "results",
        experiment_id: str = "default_experiment",
        timeout: float = 60.0,
        session: requests.Session | None = None,
    ) -> None:
        self.endpoint_url = endpoint_url
        self.results_dir = results_dir
        self.experiment_id = experiment_id
        self.timeout = timeout
        self.session = session or requests.Session()
        os.makedirs(self.results_dir, exist_ok=True)

    def get_default_jsonl_path(self) -> str:
        """Returns the default JSONL log path based on experiment_id."""
        return os.path.join(self.results_dir, f"{self.experiment_id}.jsonl")

    def send_request(
        self,
        prompt: str,
        session_id: str,
        turn_index: int = 0,
        cache_prompt: bool = True,
        n_predict: int = 64,
        temperature: float = 0.0,
        output_jsonl_path: str | None = None,
    ) -> BenchmarkRecord:
        """
        Send a single completion request to llama-server and record metrics.
        """
        request_id = f"req-{uuid.uuid4().hex[:8]}"
        payload = {
            "prompt": prompt,
            "n_predict": n_predict,
            "temperature": temperature,
            "cache_prompt": cache_prompt,
        }
        timestamp = datetime.now(timezone.utc).isoformat()

        t0 = time.perf_counter()
        timings: dict[str, Any] = {}
        response_text = ""
        status = "success"
        error_msg: str | None = None

        try:
            resp = self.session.post(
                self.endpoint_url,
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            response_text = data.get("content", data.get("text", ""))
            timings = data.get("timings", {})
        except Exception as e:
            status = "error"
            error_msg = str(e)
        finally:
            wall_time_s = time.perf_counter() - t0

        record = BenchmarkRecord(
            request_id=request_id,
            session_id=session_id,
            turn_index=turn_index,
            prompt=prompt,
            cache_prompt=cache_prompt,
            wall_time_s=wall_time_s,
            timings=timings,
            response_text=response_text,
            status=status,
            timestamp=timestamp,
            experiment_id=self.experiment_id,
            error=error_msg,
        )

        # Write to JSONL log
        target_path = output_jsonl_path or self.get_default_jsonl_path()
        target_dir = os.path.dirname(os.path.abspath(target_path))
        if target_dir:
            os.makedirs(target_dir, exist_ok=True)

        with open(target_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(record)) + "\n")

        return record

    def run_interleaved_sessions(
        self,
        sessions: list[SessionScript],
        cache_prompt: bool = True,
        output_jsonl_path: str | None = None,
        progress_callback: Callable[[BenchmarkRecord], None] | None = None,
    ) -> list[BenchmarkRecord]:
        """
        Execute N sessions in round-robin interleaved turn order.
        """
        schedule = generate_interleaving_order(sessions)
        records: list[BenchmarkRecord] = []

        for session_id, turn_idx, turn in schedule:
            rec = self.send_request(
                prompt=turn.prompt,
                session_id=session_id,
                turn_index=turn_idx,
                cache_prompt=cache_prompt,
                n_predict=turn.n_predict,
                temperature=turn.temperature,
                output_jsonl_path=output_jsonl_path,
            )
            records.append(rec)
            if progress_callback:
                progress_callback(rec)

        return records

