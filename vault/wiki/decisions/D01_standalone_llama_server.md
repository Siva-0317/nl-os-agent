# D01: Bundled llama-server vs LM Studio or Python Bindings

*Status: Approved*  
*Date: 2026-11-08*  
*Author: Antigravity Agent*

---

## 1. Context
Previous prototype iterations depended on an external LM Studio GUI application hosting the model on `localhost:1234`. This introduced several fatal product limitations:
1. Required non-technical users to manually download and configure an external third-party GUI.
2. Prevented automated lifecycle management, hidden execution, and dynamic port allocation.
3. Python bindings (`llama-cpp-python`) frequently suffer from compilation failures on clean Windows machines without MSVC C++ Build Tools installed.

---

## 2. Options Considered
- **Option A**: Continue requiring LM Studio with manual setup instructions.
- **Option B**: Bundle precompiled `llama-server.exe` binaries directly for Windows x64 (CPU, Vulkan, and CUDA builds).
- **Option C**: Compile `llama-cpp-python` wheels in-process.

---

## 3. Decision
Adopt **Option B**: Supervise official precompiled `llama-server` as a hidden subprocess launched with `CREATE_NO_WINDOW`, bound to a random free localhost port with a 32-character hex API key.

---

## 4. Consequences & Evidence
- **Installer Autonomy**: The agent can run standalone on any clean Windows machine without administrator privileges.
- **Dynamic Fallback**: If GPU VRAM is exhausted or drivers fail, the manager automatically restarts the server in CPU mode (`-ngl 0`).
- **Validated In**: [`tests/test_runtime_manager.py`](../../tests/test_runtime_manager.py) and verified in [`docs/evidence_ledger.md`](../../docs/evidence_ledger.md).

