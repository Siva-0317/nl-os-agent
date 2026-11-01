#!/usr/bin/env python3
"""
Root entry point for NL OS Agent.
Interactive CLI for natural language terminal command execution.
"""

import importlib.util
import os
import sys

# Ensure project root, src, and legacy modules are accessible
root_dir = os.path.dirname(os.path.abspath(__file__))
legacy_dir = os.path.join(root_dir, "legacy")
src_dir = os.path.join(root_dir, "src")

for path in (legacy_dir, src_dir, root_dir):
    if path not in sys.path:
        sys.path.insert(0, path)


def main():
    legacy_agent_path = os.path.join(legacy_dir, "agent.py")
    if os.path.exists(legacy_agent_path):
        spec = importlib.util.spec_from_file_location("legacy_agent", legacy_agent_path)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod.main()
    print("NL OS Agent CLI initialized.")


if __name__ == "__main__":
    main()
