# NL-OS-Agent: Plan to Make It Usable by Anyone, and to Push the Research Further

Based on `CLAUDE_CONTEXT_HANDOVER_AND_NEXT_STEPS.md` (state as of Nov 3, 2026).

---

## 0. Summary

1. **Fix the evidence first (3 to 4 days).** Several numbers in the handover cannot be reported as they stand (section 1). Shipping an installer on top of unreliable results would weaken both the product and the paper.
2. **Make it standalone.** Replace LM Studio with a bundled `llama-server`, hardware detection and a model manager. Official llama.cpp releases already provide Windows x64 builds for CPU, CUDA, Vulkan, SYCL and HIP, so one app can cover "with or without GPU".
3. **Add Windows-native safety coverage.** Non-technical users run PowerShell and cmd, not Bash. Check that the effect engine understands them.
4. **Build a friendly shell:** tray icon, global hotkey, a small window that explains actions in plain English, undo, and a first-run wizard. Keep `nlctl` and the shell hooks for developers.
5. **Package** as a per-user installer (no admin rights), with the model downloaded on first run (or an offline installer).
6. **Test with non-technical people**, including whether they understand the previews. That is the strongest new research result available to you.
7. Optional stretch: multi-step plans with a virtual file system, voice, a larger public benchmark.

---

## 1. Review of the handover

### 1.1 What is solid
- Two-stage `propose()` / `commit()` with a re-check before execution, a TOCTOU test, quarantine with byte-exact undo, and a provenance module.
- `nlctl` with hooks, an end-to-end demo, and a clean git history.
- A clear architecture and deterministic code deciding safety, with the LLM only proposing.

### 1.2 What must be fixed before you report or ship

| # | Finding | Evidence | Action |
|---|---|---|---|
| H1 | **E5 table cannot be reported as is.** The confidence intervals [40.9, 92.8] and [13.7, 69.4] are exactly those for 6/8 and 3/8, so each condition used **8 cases**, not 32. For 24/32 the interval would be [57.9, 86.7] | I recomputed them | Re-run E5 with at least 100 cases per condition |
| H2 | **The McNemar claim is impossible.** With 8 paired cases and a 3-case difference (6/8 vs 3/8), the smallest possible exact p-value is 0.25, not below 0.05 | Enumerated all outcomes | Remove the claim; recompute on the larger run |
| H3 | **Latencies look simulated.** Exactly 120.0 ms and 25.0 ms; ratio 120/520 = 0.231 ms per token and 25/110 = 0.227 ms per token (almost linear); identical accuracy (75.0%) in three conditions. A 520-token prefill in 120 ms on a 4 GB laptop GPU is also implausibly fast for a 4B model | Arithmetic | Check each results row for the backend name; if `DemoMockBackend` or a simulator, discard and re-run live |
| H4 | **The safety benchmark is tiny.** 15 attacks and 15 benign commands; 0/15 means the true bypass rate could be as high as 21.8% (95% interval). "Eliminated all adversarial evasions" is not supported. The earlier plan asked for 300+ attacks | CP interval | Build at least 300 blind attacks and 1,000 benign commands (section 5, P0) |
| H5 | **G2 is still shown as "100% bypass".** That is true by definition (it never intercepts) and reads as a strawman | Table | Report G2 on a different metric, and add a G4 baseline: an LLM judging command safety |
| H6 | **Test count dropped from 60 to 39**, and `tests/run_all.py` is a custom runner | Two handovers | Confirm `pytest -q` collects every test, including earlier layout, state, prefix-cache and harness tests |
| H7 | **Silent mock fallback** (`DemoMockBackend`) | Handover section 4 | Fine for tests; for the product it must never run silently. Show "model not available" instead, and stamp the backend name on every result row |
| H8 | **Model auto-selection by name** (`finetun`/`dense` in LM Studio) | Handover section 4 | Replace with an explicit model manifest in the product |
| H9 | **Which checkpoint is the active GGUF?** The handover says a 30-step smoke test, then proposes a longer run | Handover sections 1, 8 | Record the training steps, data version and hash for the model you ship |
| H10 | **Windows shell coverage is unknown.** Parsing logic is POSIX style (`shlex`, `rm`, `mv`). Windows users type `Remove-Item`, `del`, `rd`, `robocopy /MIR`, etc. | Handover shows PowerShell hooks | Test now; build coverage in P1 |
| H11 | **Training loss targets** ("below about 0.40") are not a success criterion | Handover section 8 | Judge by held-out execution accuracy and JSON validity |
| H12 | E0 real results are not quoted | Handover section 2 | Put the real `prompt_n`, `cache_n`, `prompt_ms` and mismatch rate in the report |

---

## 2. Product definition

### 2.1 Users
| Persona | Needs |
|---|---|
| Everyday user (main target) | Describe a task in plain English; see what will happen in plain English; undo; never see a shell unless they want to |
| Power user / developer | Terminal hooks, `nlctl`, visible commands |
| Low-end PC owner | Works without a GPU, with limited RAM, no admin rights |

### 2.2 Requirements
- Install in under 10 minutes on a clean Windows 10/11 PC without admin rights; one-click uninstall.
- Works offline after the model download; no account; no telemetry by default.
- With or without GPU: automatic backend and settings selection.
- No technical vocabulary in the default UI; command text available under "Show details".
- Safe by default: nothing except read-only actions runs without a clear preview and a click.
- Every destructive action is undoable for a stated period (7 days suggested).

### 2.3 Hardware tiers (set targets after you measure; these are proposals)
| Tier | Example machine | Configuration | Goal for request-to-preview time |
|---|---|---|---|
| L | 4 to 8 GB RAM, no GPU | CPU build, smaller quant, short prompt, optional template-only "Lite" mode | about 12 s or less |
| M | 8 to 16 GB RAM, integrated GPU | CPU or Vulkan build, Q4_K_M | about 6 s or less |
| H | NVIDIA/AMD GPU, 4 GB or more | CUDA/Vulkan with layer offload | about 3 s or less |

On CPU, prompt processing is the slow part, so your short-prompt fine-tune and prefix cache should matter most on tier L. Measure this: it makes a strong result.

---

## 3. Architecture

```mermaid
flowchart TD
    subgraph UI["User-facing (unprivileged)"]
        TR[Tray icon + global hotkey]
        PW[Popup window\nplain-English preview\nRun / Cancel / Details]
        HX[History + Undo + Settings]
        WZ[First-run wizard + Doctor]
        CLI[nlctl + shell hooks\n(developers)]
    end

    subgraph CORE["Core service (one background process)"]
        API[Local API\n127.0.0.1 + token]
        PRO[propose / commit]
        EFF[Effect engine\nBash + PowerShell + cmd parsers]
        REN[Plain-English renderer\n(deterministic)]
        QUA[Quarantine + undo]
        CAC[Verified cache + state]
    end

    subgraph RT["Runtime manager"]
        HW[Hardware detection]
        LS[llama-server child process\nCPU / Vulkan / CUDA]
        MM[Model manager\nmanifest + sha256 + resume]
    end

    TR --> PW --> API
    HX --> API
    WZ --> MM
    CLI --> API
    API --> PRO --> CAC
    PRO --> EFF --> REN
    PRO --> LS
    HW --> LS
    MM --> LS
    PRO --> QUA
```

Key points:
- **One core service** serves the UI, CLI and hooks. This also fixes the cold-start cost of starting a new process for every hook call.
- **llama-server is a child process** of the app, bound to localhost on a random free port with an API key. The backend already talks to `llama-server`, so little code changes.
- **The plain-English text is generated by deterministic code from the effect result**, not by the LLM, so safety-critical wording cannot be hallucinated.

---

## 4. Key decisions (recommended choices)

| Topic | Options | Recommendation | Why / caution |
|---|---|---|---|
| Inference runtime | `llama-cpp-python`; bundled `llama-server` | **Bundled official `llama-server`** (CPU + Vulkan; CUDA as an optional download) | Prebuilt Windows zips exist for CPU, CUDA, Vulkan and others; Python bindings can lag behind new architectures. llama.cpp is MIT licensed |
| GPU choice | Detect and choose | Detect with `llama-server --list-devices` (check your build) and `nvidia-smi`; start CPU if detection or a short load test fails | Vulkan covers many GPU vendors; test on real machines |
| UI stack | PySide6 (Qt), pywebview + local web UI, Tauri/Electron | **PySide6** | Same language as the core; Qt for Python is LGPL, so respect dynamic linking and notices |
| Install format | PyInstaller one-file, one-folder, Nuitka | **PyInstaller one-folder + Inno Setup installer**, per-user | One-file builds start slower and are flagged by antivirus more often (commonly reported) |
| Model delivery | Bundle in installer; first-run download | **First-run download with checksum and resume**; offline installer as a second download | GitHub release assets are limited to 2 GiB each; host the GGUF on Hugging Face |
| Signing | Unsigned; paid certificate; winget/Store | Start unsigned with clear instructions; consider a certificate or winget before wide release | SmartScreen warnings are the biggest friction for non-technical users |
| Platform | Windows first; macOS/Linux later | **Windows 10/11 first** | Most non-technical users; llama.cpp has Metal/Linux builds for later |
| Elevation | Run as admin; never | **Never run as admin.** If an action needs it, explain and let Windows show its own prompt | Do not build the admin broker until the core is stable |

### Licensing and distribution checklist (not legal advice)
- NVIDIA's Open Model License text (as published on NVIDIA model pages) allows creating and distributing derivative models, and asks that recipients get a copy of the agreement plus a Notice file saying "Licensed by NVIDIA Corporation under the NVIDIA Open Model License". Some NVIDIA models also require a "Built on ..." mention. **Read the exact license file in the Nemotron-3-Nano-4B repository**, ship the notice in the installer and on the model page, and note the indemnity and update clauses.
- Include licenses for llama.cpp (MIT), PySide6 (LGPL), and every Python dependency; generate a `THIRD_PARTY_NOTICES` file.
- Publish a model card: training data (synthetic plus any public sets with licenses), intended use, limitations, evaluation numbers, and the safety design.
- Privacy statement: all processing is local; list what is stored in `%LOCALAPPDATA%\NLAgent`.

---

## 5. Phase plan

### P0: Evidence repair and freeze (3 to 4 days)
- Re-run E5 live with at least 100 cases per condition from a sealed set of 200+; add an exact McNemar test only on paired outcomes; print n for every cell.
- Make `make_figures.py` and every results writer refuse rows whose backend is a mock/simulator.
- Confirm `pytest -q` collects everything (H6).
- Run `llama-bench` and a live latency breakdown on the RTX 2050, and repeat the same run with GPU layers set to 0 to get a **CPU-only baseline**.
- Paste the real E0 numbers into the doc.
- Tag `v0.2-evidence-repaired`.

### P1: Safety coverage for Windows and a bigger benchmark (week 1 to 2)
- **Parsers:** Bash via a real parser (for example `bashlex`); PowerShell via its own AST (`[System.Management.Automation.Language.Parser]::ParseInput`) through a long-lived helper process to avoid start-up delay; cmd.exe verbs with a small grammar.
- **Effect models to add:** `Remove-Item` (`-Recurse`, `-Force`, wildcards, `-LiteralPath`), `del`/`erase` (`/s /q /f`), `rd`/`rmdir /s /q`, `Move-Item`/`move`, `Copy-Item -Force`/`copy`, `robocopy /MIR /PURGE`, `xcopy`, `Set-Content`/`Out-File`/`>`, `Add-Content`/`>>`, `Clear-Content`, `Rename-Item`, `New-Item`, registry writes (`reg`, `Set-ItemProperty`), `Stop-Process`/`taskkill`, `Stop-Service`, `Format-Volume`/`diskpart`/`format` (hard block), `Invoke-Expression`, `-EncodedCommand`, download-and-execute chains, `Start-Process -Verb RunAs`, .NET calls such as `[IO.File]::Delete`, and aliases (`ri`, `rm`, `del`).
- **Protected locations:** `C:\Windows`, `C:\Program Files*`, `C:\ProgramData`, drive roots, other users' profiles; treat deletion in Documents, Desktop, Pictures and OneDrive as destructive with quarantine.
- **Benchmark v3:** at least 300 attacks written blind by teammates who have not read the engine (per shell), at least 1,000 benign commands, Clopper-Pearson intervals, and guards G0-simple, G0-published, G1, G2 (reported honestly), **G4 = LLM judge**, G3. Run once with the engine frozen; list every miss.
- **System-level harm test:** 500 natural-language requests through the full pipeline (model plus effect engine) in temporary fixtures; count harmful effects that ran without confirmation.

### P2: Standalone runtime (week 2 to 4)
- Runtime manager: detect RAM, CPU cores and GPUs; choose backend, layer offload, threads and context size; start `llama-server` hidden on a random localhost port with an API key; health checks, restart on crash, optional idle unload to free RAM.
- Model manager: manifest with name, quant, size, sha256, minimum RAM, license notice; resumable download; verify hash; import from a local file for offline use.
- Core service API and token; the CLI, hooks and UI become clients (removes per-call cold start).
- Remove the silent mock fallback and the name-based model picker; add an explicit "no model" state and a dev-only `--mock` flag.
- `nlagent doctor` command: checks model hash, backend, ports, disk space, permissions, and runs a 1-prompt latency test; "Copy diagnostics" with redaction.
- **Acceptance:** on a clean Windows VM without LM Studio, `doctor` passes and a request shows a preview on CPU only.

### P3: Friendly UI (week 3 to 6)
- Tray icon, global hotkey (configurable), popup with an input box and a result card.
- **Card content:** a one-sentence summary ("Move 12 files from Downloads to Documents\Invoices, about 18 MB"), a safety badge, an expandable file list, "Show command", and buttons Run / Cancel / Edit request.
- **Risk communication rules:** describe consequences, not commands; Cancel is the default button for destructive actions; for large operations (for example 50 or more files, or over a size threshold) require typing the count; never use a generic "Are you sure?".
- History tab with Undo buttons and the remaining undo time; settings (safety level, hotkey, startup, model, privacy); first-run wizard (hardware check, model download with progress, short tutorial in a "playground" folder).
- Accessibility basics: keyboard navigation, scalable fonts, contrast, screen-reader labels.
- Safety levels: **Careful (default)** confirms anything that changes files; **Standard** confirms destructive and ambiguous actions; **Expert** shows commands and hooks.
- **Acceptance:** a participant who has never seen the app can complete a read-only task, a create task and a delete-with-undo task unaided.

### P4: Packaging and distribution (week 5 to 7)
- PyInstaller one-folder build; bundle llama-server CPU and Vulkan; CUDA pack as an optional download.
- Inno Setup per-user installer: Start menu, optional autostart (`HKCU` Run key), uninstaller that stops the service and asks whether to keep quarantine data.
- Optional: Explorer context-menu entry ("Ask NL Agent about this"); opt-in PowerShell profile hook installer with backup and reversal.
- CI on GitHub Actions (Windows runner): tests, build, silent-install smoke test, checksum file.
- Test installs in disposable environments (Windows Sandbox if your edition has it, or VMs): Windows 10 and 11, low-spec VM, no admin rights, network drop during download.
- Check VirusTotal for false positives; report false positives to vendors; avoid one-file builds.
- **Known friction to document:** SmartScreen "unrecognized app" warning if unsigned; Windows Defender Controlled Folder Access may block changes to Documents/Pictures; OneDrive Files On-Demand placeholders may behave unexpectedly with hardlink-based quarantine (test early; fall back to copy).
- **Acceptance:** three clean machines or VMs install, run, undo and uninstall without admin rights.

### P5: Everyday-task scope, data and fine-tune round 2 (week 4 to 8, in parallel)
- Intent catalog: organize Downloads, find files by name/date/type, free disk space, rename in bulk, zip/unzip, copy to USB, show what uses disk or network, list or close apps, simple system information.
- Dataset v3 with non-technical phrasing ("my pictures", "that thing I downloaded yesterday"), typos, vague references, time words mapped to file times, Windows folder names, PowerShell/cmd/Bash variants, clarification questions, and refusals for requests that cannot be done safely.
- Fine-tune on Colab (or Kaggle) in chunks with checkpoints on Drive; evaluate by execution accuracy on 300+ sealed cases across shells with intervals; compare to the current model.
- **Quantization tiers:** Q4_K_M default, a smaller quant for low RAM, and measure accuracy loss and CPU speed on the sealed set.
- Optional "Lite" mode with no LLM: retrieval and templates for the most common 100 tasks, instant and tiny.

### P6: Multi-step plans with a virtual file system (stretch, week 7 to 9)
- The model proposes a plan of up to 10 steps (JSON). Step k's effects are computed on a **virtual overlay** of the file system after steps 1 to k-1, so the preview of the whole plan is accurate.
- Show one combined preview and one undo group; stop on failure and roll back from quarantine; start with file operations only.
- **Evaluate:** predicted vs actual file changes on 100 plans; report exact-match rate.

### P7: Usability study and hardware matrix (week 8 to 10)
See section 7 and the hardware tiers in 2.3. Test on at least three real machines plus a CPU-only configuration.

### P8: Release and write-up (week 10 to 12)
- `v1.0` tag; installer and model on public hosting; README with GIFs, FAQ, troubleshooting; 2-minute demo video; model card; `THIRD_PARTY_NOTICES`.
- Report and paper updates with measured data only; update the limitations section.

---

## 6. Safety policy for non-technical users

| Situation | Behavior |
|---|---|
| Read-only | Run and show the result in plain language |
| Create or append | Show a one-line preview; run on click (Careful) or automatically (Standard) |
| Delete, overwrite, bulk move | Preview with counts and sizes; Cancel is the default; undo window shown; large operations require typing the count |
| Needs administrator rights | Explain why; never run as admin by default; Windows shows its own prompt only if the user chooses |
| Unparseable or opaque (encoded commands, download-and-run) | Explain it cannot be checked and ask for another way to describe the task, or block |
| Disk-level or registry-level operations (`format`, `diskpart`, hive deletes) | Hard block with a plain explanation |
| Vague request ("clean this up") | Ask a clarifying question; do not guess |
| Model not loaded or very slow | Say so; do not substitute a mock |

Also: all data stays on the device; logs are local with redaction; no network use by the agent except the optional model download and update check.

---

## 7. Usability and safety study (the research payoff)

**Design (pilot):** 10 to 12 non-technical adults; informed consent; a fixture folder with fake files so no personal data is involved; follow your department's rules on human participants.

**Tasks:** 8 tasks including two where a careless approval would cause harm (for example, the plan also removes files the participant said to keep).

**Between-subjects comparison:** half see the **plain-English effect preview** (your design), half see the **raw command** (what other tools show).

| Measure | How |
|---|---|
| Task success and time | Observed |
| Preview comprehension | After each preview, ask "What will happen if you press Run?" (multiple choice); percentage correct |
| **Unsafe approval rate** | Share of harmful actions approved |
| Usability | System Usability Scale (10 items) |
| Trust calibration | Rating before and after the harmful-case tasks |

With 10 to 12 people, report descriptive results and effect sizes, and state clearly that it is a pilot. This is a human-factors finding that most agent papers lack.

---

## 8. Higher-reach research directions (pick two)

| # | Direction | Contribution |
|---|---|---|
| 1 | Plan-level effect analysis on a virtual file system (P6) | Algorithmic; preview accuracy metric |
| 2 | Plain-English preview vs raw command: comprehension and unsafe-approval study (section 7) | Human factors |
| 3 | Low-resource latency study: CPU-only, quantization, short prompts and caching together, with a Pareto plot | Systems; strongest on tier L machines |
| 4 | Public benchmark release: attack and benign command sets per shell, with scoring code, plus the G4 LLM-judge baseline | Community value, citations |
| 5 | Cross-shell effect engine built on native parsers | Engineering rigour |
| 6 | Voice input with a spoken challenge phrase for destructive actions | Accessibility; extra safety design |
| 7 | macOS/Linux ports | Reach |

---

## 9. Agent prompts (Antigravity)

Run each in WSL2 or a Windows VM; tests must use temporary directories only. Review every diff in `safety/`.

**T25: evidence repair.**
"Update scripts/run_e5.py: accept --n-per-condition and refuse to run if the backend name contains mock or demo. Print n for every cell, compute Wilson intervals from those n, and compute McNemar only on paired outcomes of the same cases. Update make_figures.py to refuse rows with a mock backend. Add tests for both refusals and for interval/McNemar values against known numbers."

**T26: test discovery.**
"Make `pytest -q` collect every test under tests/. List tests present in git history but no longer collected. Replace tests/run_all.py with a thin wrapper around pytest. Report the final count."

**T27: safety bench v3.**
"Create scripts/run_safety_bench_v3.py reading datasets/safety_v3_attacks_{bash,powershell,cmd}.jsonl and datasets/benign_v3.jsonl. Guards: G0-simple, G0-published, G1, G2 (reported as 'no preview, no tiering' with in-scope damage counted), G3, and G4 (LLM judge using the local model with a fixed prompt). Report hard-blocked / escalated / auto-run, bypass and false-alarm rates with Clopper-Pearson intervals, confirmation burden per 100 benign, per-technique breakdown, and the engine git_sha."

**T28: Windows shell coverage.**
"Add src/nlagent/safety/ps_parser.py using a persistent powershell helper that returns the AST as JSON, and src/nlagent/safety/cmd_parser.py for cmd.exe verbs. Extend effects.analyse with models for Remove-Item, del, rd, Move-Item, Copy-Item, robocopy /MIR, Set-Content, Out-File, Clear-Content, registry writes, Stop-Process, taskkill, format/diskpart (hard block), Invoke-Expression, -EncodedCommand and Start-Process -Verb RunAs. Include aliases. Tests build fixtures in temp directories and only call analyse (never execute)."

**T29: runtime manager.**
"Create src/nlagent/runtime/manager.py: detect RAM, CPU cores and GPUs (via `llama-server --list-devices` and nvidia-smi when present); select CPU, Vulkan or CUDA build and layer offload; start llama-server hidden on a random free localhost port with an API key; health check, restart on crash, optional idle unload; fall back to CPU if a GPU load test fails. Unit tests use a fake executable. Add `nlagent doctor`."

**T30: model manager.**
"Create src/nlagent/runtime/models.py: read models/manifest.json (name, quant, size, sha256, min_ram_gb, notice_file); resumable download with HTTP range requests; sha256 verification; import from local file; store under %LOCALAPPDATA%/NLAgent/models. Tests use a local HTTP server, including an interrupted download."

**T31: core service.**
"Refactor so one background core service exposes propose/commit/undo/history on 127.0.0.1 with a token file readable only by the user. nlctl and hooks become thin clients. Remove DemoMockBackend from the default path (keep behind --mock for tests) and remove name-based model selection. Add tests that the service refuses requests without the token and binds only to localhost."

**T32: plain-English renderer.**
"Create src/nlagent/ui/render.py: deterministic functions mapping an Effect to a one-sentence summary, a safety badge, an expandable item list, and an undo notice (counts, sizes, folder names, no shell jargon). Golden-string tests for delete, overwrite, move, create, opaque, protected path and privileged cases."

**T33: UI.**
"Create src/nlagent/ui/app.py with PySide6: tray icon, configurable global hotkey, popup with input, result card (summary, badge, details, Run/Cancel/Edit), history and undo tab, settings. Cancel is the default button for destructive actions; typing the item count is required above a configurable threshold. Use pytest-qt tests with a fake core client."

**T34: first-run wizard.**
"Add a first-run wizard: hardware check and recommendation, model download with progress and resume, a playground folder with sample files, a 3-step tutorial, hotkey and autostart choices, and a final doctor run. Persist completion in the config file."

**T35: packaging.**
"Add packaging/nlagent.spec (PyInstaller onedir), packaging/installer.iss (Inno Setup, per-user, optional autostart, uninstall stops the service and asks about quarantine data), and .github/workflows/build.yml on windows-latest that runs tests, builds, runs a silent install smoke test and publishes a sha256 file. Generate THIRD_PARTY_NOTICES from installed dependencies and include the NVIDIA model notice text."

**T36 (stretch): plans with virtual file system.**
"Create src/nlagent/plan/engine.py: a plan is up to 10 steps; maintain a copy-on-write metadata overlay of the file tree; compute each step's effect on the overlay; produce one combined preview and one undo group; stop and roll back on failure. Tests compare predicted versus actual manifest diffs on 50 generated plans in temp directories."

**T37: hardware matrix.**
"Create scripts/run_hw_matrix.py: for each quantization and backend setting available, run 100 requests from the sealed set and record execution accuracy, prefill and decode speed, time to preview, peak RAM and VRAM; write results with provenance and a Pareto plot of latency versus accuracy."

---

## 10. Risks and fallbacks

| Risk | Fallback |
|---|---|
| SmartScreen and antivirus warnings | Clear instructions; winget or a signing certificate; avoid one-file builds |
| 4B model too slow on tier L | Smaller quant, short prompt, template-only Lite mode |
| Users misunderstand previews | Redesign wording from the pilot; typed-count confirmation for large operations |
| Windows parsing gaps | Escalate anything the parser cannot understand; never auto-run it |
| Hardlink quarantine problems with OneDrive placeholders or cross-volume files | Copy fallback with a size cap; test early |
| Controlled Folder Access blocks writes | Detect and explain; document how to allow the app |
| Scope growth | Order: P0, P1, P2, P3, P4 are required; P5 data and fine-tune; P6, voice, ports are optional |

---

## 11. Definition of done for v1.0

- [ ] All reported numbers come from live backends with provenance; E5 uses at least 100 cases per condition; safety v3 has at least 300 blind attacks and 1,000 benign commands, with intervals.
- [ ] PowerShell and cmd coverage tested; every miss listed.
- [ ] App runs without LM Studio on CPU-only and GPU machines; `doctor` passes on a clean VM.
- [ ] A new user completes the first-run wizard and three tasks unaided.
- [ ] Undo works for all destructive actions in the UI; Cancel is the default.
- [ ] Installer works without admin rights on three clean setups; uninstall is clean.
- [ ] Model notice, third-party notices and privacy statement shipped.
- [ ] Pilot study completed and reported with its limits.
- [ ] Demo video and README with troubleshooting.
