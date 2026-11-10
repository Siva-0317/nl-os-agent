"""
quick_propose.py - Thin CLI helper for terminal hooks (Bash Ctrl+G / PowerShell).

Usage:
    python -m nlagent.cli.quick_propose "<natural language request>" [cwd]
Outputs the proposed command to stdout so the shell hook can replace the buffer.
"""

from __future__ import annotations

import os
import sys

# Ensure src is in sys.path
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_src = os.path.join(_root, "src")
for p in (_root, _src):
    if p not in sys.path:
        sys.path.insert(0, p)

from nlagent.backends.llama_server import LlamaServerBackend, resolve_local_endpoint
from nlagent.cache.verified_cache import VerifiedCommandCache
from nlagent.context.state import SessionState
from nlagent.daemon.propose import get_default_env, propose


def main():
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        sys.exit(0)

    user_request = sys.argv[1].strip()
    cwd = sys.argv[2] if len(sys.argv) > 2 and os.path.isdir(sys.argv[2]) else os.getcwd()

    # Try querying background service first to eliminate process startup latency
    try:
        from nlagent.service.core import ServiceClient
        client = ServiceClient()
        if client.is_service_running():
            res = client.propose(request=user_request, cwd=cwd)
            cmd = res.get("command", "").strip()
            if cmd:
                sys.stdout.write(cmd)
                sys.stdout.flush()
                return
    except Exception:
        pass

    session = SessionState(cwd=cwd)
    cache = VerifiedCommandCache(threshold=0.88)
    backend = LlamaServerBackend(endpoint_url=resolve_local_endpoint())
    env = get_default_env(cwd=cwd)

    try:
        proposal = propose(
            request=user_request,
            session_state=session,
            backend=backend,
            verified_cache=cache,
            env=env,
        )
        command = proposal.get("command", "").strip()
        if command:
            sys.stdout.write(command)
            sys.stdout.flush()
    except Exception as exc:
        # On failure, don't corrupt the shell buffer; print error to stderr
        sys.stderr.write(f"\n[nlagent error]: {exc}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()

