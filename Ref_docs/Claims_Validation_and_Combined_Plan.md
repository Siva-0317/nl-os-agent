# Claims, Novelty and Patent Reality Check, and the Combined Plan

This file answers three questions, then adds a **validation track** that runs alongside the productization plan (`Productization_and_Next_Level_Plan.md`). It is not legal advice.

---

## 1. Direct answers

**1. Did the fine-tuning achieve what it was supposed to do?**
Not demonstrated. Some goals are probably partly met, but none is proven by valid data yet (section 2). In particular:
- Only **30 training steps** are documented (480 examples, 6.7% of one epoch). It is a format adapter at best. Check which checkpoint your active GGUF is.
- The only accuracy table (E5) uses **8 cases per condition**, has latencies that look simulated, and reports a p-value that is mathematically impossible for those numbers.
- Training loss going from 3.21 to 0.81 shows the model learned your template format; it does not show better commands on new requests.

**2. Can you claim this as new, and as an invention with a performance advantage over other work?**
- **As a research contribution: yes, a narrow one, after validation.** The defensible core is "deterministic effect analysis with preview, quarantine and re-check, evaluated against denylist and LLM-judge guards, on a local small model". Right now you can claim a **design plus preliminary results**.
- **As a patent: I cannot tell, and neither can the paper.** Patentability is a legal question about prior art and technical character. Several parts of your system have strong adjacent prior art (section 4). Use the word "contribution" in the paper, not "invention", and take the patent question to your college's IPR cell and a patent attorney before you publish more (section 5).
- **"Better performance than other papers" is only claimable on a benchmark where you ran the other approaches yourself.** Numbers from other papers are context, not a comparison.

**3. Can you proceed with the earlier plan as it is?**
**Partly.** The productization work (standalone runtime, UI, installer) is worth doing and does not depend on the claims. But the earlier plan has no track whose job is to *earn* the claims. Without it you will ship an app and still have a paper you cannot defend. So: **run both tracks, with gates** (section 8).

---

## 2. Evidence status of the fine-tuning

| Intended purpose | Evidence in the handover | Status |
|---|---|---|
| Learn the output format (JSON, clarifying question) | Loss 3.21 to 0.81 over 30 steps; no JSON-validity rate reported | Probably partly met; unmeasured |
| Better command accuracy than the base model | E5: Base+Full = Tuned+Full = 75.0% (6/8 each) | **No evidence of improvement** |
| Context distillation: short prompt matches full-prompt accuracy | E5: Tuned+Short 75.0% vs Base+Short 37.5% (6/8 vs 3/8) | Suggestive at best; n=8, timings look simulated, p<0.05 claim impossible (smallest possible exact p is 0.25) |
| Correct commands per shell (Bash vs PowerShell vs cmd) | Nothing reported | Unknown |
| Lower latency (fewer prompt and output tokens) | 520 to 110 prompt tokens (-78.8%) is a prompt-design fact. Timings are exactly 120.0 and 25.0 ms, proportional to token count | Token reduction true by construction; latency unproven |
| Generalises beyond your templates | No human-written or public test results | Unknown; template-overfitting risk |

What a fair reading says: tuned+full equalling base+full is normal (a strong base model with an explicit prompt does well). The *interesting* hypothesis is **non-inferiority at a much shorter prompt**. That is a modest, practical, testable claim. Context distillation itself is an established technique (to my knowledge introduced in Askell et al. 2021 and Snell et al. 2022; verify before citing), so your contribution would be applying it to this task and measuring it carefully, not the method.

---

## 3. Claim ladder (what you may say now, and what upgrades it)

| # | Claim | Status | Wording allowed **now** | Gate to upgrade |
|---|---|---|---|---|
| C1 | Effect-based tiering stops evasions that denylists miss | Preliminary: 0/49 (v1, upper bound 7.3%), 0/15 (v2, upper bound 21.8%); cases written by the team; static analysis only | "In a preliminary evaluation (n=49 and n=15) all attacks were escalated, while denylist baselines missed 49% to 73%; intervals are wide" | 200+ blind attacks labelled by actual effect, adaptive attacker, ablations (section 6, V2) |
| C2 | Lower false-alarm burden than denylists | 13.3 per 100 on 15 benign commands | Do not claim | 1,000+ benign commands |
| C3 | Fine-tuning achieves parity at 79% fewer prompt tokens | Not valid yet | "Prompt length reduced from about 520 to 110 tokens by design; accuracy parity is being evaluated" | Pre-registered non-inferiority test, n >= 200 (V1) |
| C4 | 4.8x faster time to first token | Likely simulated | Do not claim | Live measurement with provenance (V3) |
| C5 | GPU prefix cache 4.5x speedup | Unverified | Do not claim | E0/E1 on the real server |
| C6 | Verified-command cache answers in about 0.3 ms | Plausible (local code) | "Cache lookups take under a millisecond in our implementation" after a provenance-stamped microbenchmark | Microbenchmark |
| C7 | TOCTOU re-check blocks races | Unit-tested | "Our tests show commit re-checks changed targets (5 tests)" | More scenarios (symlink, rename, permission change) |
| C8 | Byte-exact undo | 9 unit tests | "Undo restored files byte-for-byte in our tests (hardlink and copy cases)" | Cross-volume, OneDrive, large-tree tests |
| C9 | Works offline on a 4 GB GPU | Not documented | Do not claim until measured | `llama-bench` and live runs on the device |
| C10 | First/novel unified system | Unchecked | "To the best of our knowledge, among the systems we reviewed" | Documented prior-art search (V4) |
| C11 | Users understand previews | No data | Do not claim | Pilot (V5) |

Words to avoid until earned: "eliminates", "proves", "first", "invention", "significantly" (without a test), "outperforms" (without the same benchmark and baselines).

---

## 4. Novelty reality check

### 4.1 What is genuinely close to what you did

| Prior work / practice | Overlaps with | What it means for you |
|---|---|---|
| PowerShell `-WhatIf` / `-Confirm`; `rsync --dry-run`; `git clean -n`; Terraform `plan` | Preview of effects before running | The *idea* of a preview is old. Your difference is automatic derivation for arbitrary model-generated commands across shells, and tiering from that |
| Recycle Bin, `trash-cli`, Volume Shadow Copy, filesystem snapshots | Undoing deletions | Undo is old; per-request hash-verified undo integrated with an agent is incremental |
| Codex CLI, Claude Code, Gemini CLI sandboxes and approval modes | Containment plus approval for agent commands | Containment is established; they do not (to my knowledge) show a derived effect preview, but verify their current behavior |
| "Before the Tool Call: Deterministic Pre-Action Authorization for Autonomous AI Agents" (arXiv 2603.20953) and "Fault-Tolerant Sandboxing for AI Coding Agents: A Transactional Approach" (arXiv 2512.12806) | Gate before action; transactional execution | Read both fully before claiming novelty; they may cover similar ground |
| SHELLSIEVE (arXiv 2606.15549) | Denylist bypass discovery | Your baseline and attack source, not competitor |
| AIOS | LLM-as-OS architecture | Different layer (application-level agent runtime) |
| llm-cmd, NL2SH | NL to shell translation | Translation only |
| Marconi and related hybrid-model caching | Prefix caching for hybrid models | Server-side; yours is single-user and edge, if you measure it |
| Context distillation (Askell et al. 2021; Snell et al. 2022) | Fine-tuning to remove prompt text | Established method |

### 4.2 Where a defensible difference may lie
1. **Deterministic, shell-aware effect resolution as the safety decision**, evaluated against denylists and an LLM judge on a benchmark labelled by *actual sandbox effect*.
2. **The combination** (translation, effect preview, tiering, quarantine, TOCTOU re-check, cache that cannot skip safety) on a small local model, with ablations showing what each part contributes.
3. **Measured low-resource behavior** (CPU-only, quantization, short prompts) of that whole pipeline.
4. **Human-factors evidence**: do plain-English previews reduce unsafe approvals compared with showing the command?
5. **Released benchmark and scoring code.**

Items 1 and 4 are the strongest and the least tied to anyone else's work.

---

## 5. If you want to explore a patent (steps, not advice)

1. **Stop and ask first.** Before more public disclosure (arXiv, public GitHub, talks, the published paper), talk to your college's IPR or innovation cell and your guide. Many countries, including India and those in the EU, have absolute-novelty rules with narrow exceptions; the US has a one-year grace period. A public repository and a published paper both count as disclosure. If your repository is public, note the date it first showed the key design.
2. **Check ownership rules.** Who owns student project IP at your institution and university? This affects who may file.
3. **Prior-art search** (Google Patents, Espacenet, and India's patent search tools if your IPR cell recommends them) for: dry-run effect analysis of AI-generated commands, quarantine/undo for agent commands, command risk classification by file-system effect.
4. **Technical character.** As I understand it, Indian law excludes "computer programme per se" and algorithms, and patents on software need a technical effect; European and US practice also have limits. An attorney decides whether "method for deriving a risk tier from resolved file-system effects, preserving affected items by hardlink before execution, and re-verifying at commit" is claimable.
5. **Provisional filing.** Some systems, including India's, allow a provisional application that fixes a priority date; ask your IPR cell about cost and timing.
6. **License choice.** If you may patent, choose the repository license deliberately (Apache-2.0 includes a patent grant; MIT does not). Keep the model license obligations separate.
7. **Be realistic.** For a student project the usual value is a strong publication and open-source release. Plan for that first; treat a patent as optional.

---

## 6. Validation track (V0 to V7)

### V0. Evidence ledger and pre-registration (days 1 to 3)
- Create `docs/evidence_ledger.md`: for every number in the report, the file, rows, backend name, model hash, n, interval, and status (valid / invalid / simulated). Retire invalid numbers.
- Commit `docs/preregistration.md` **before** collecting new data. Suggested hypotheses and thresholds (edit as a team):

| ID | Hypothesis | Success rule |
|---|---|---|
| H-FT1 | Tuned+short is non-inferior to base+full on execution accuracy | Lower bound of the paired 95% interval on (tuned+short minus base+full) above -5 percentage points, n >= 200 |
| H-FT2 | Tuned model emits valid JSON more often | JSON validity at least 98%, and higher than base |
| H-FT3 | Tuned+short has lower output tokens | Median output tokens lower than base (reasoning off) |
| H-SAF1 | Effect engine bypass rate is low on blind attacks | Zero-miss run with n >= 200 gives an upper bound <= 1.83%; any misses reported honestly |
| H-SAF2 | Effect engine beats G0, G1 and G4 (LLM judge) | Paired McNemar on the same cases, p < 0.01 |
| H-SAF3 | Benign burden is acceptable | At most 5% of routine benign commands escalated (set your own target) |
| H-LAT1 | Tuned+short reduces time to preview | Median at least 30% lower than base+full on CPU-only and GPU, live runs |
| H-UX1 | Plain-English preview improves comprehension | Higher correct-answer rate than raw-command display (pilot, descriptive) |

### V1. Fine-tuning validation
- **Held-out sets:** T-a family-held-out (300), T-b human-written by people who have not seen the templates (100+), T-c public sample with license checked (200). Sealed until the final run.
- **Models:** base+full, base+short, base+few-shot, tuned+full, tuned+short, all Q4_K_M (the artifact you ship), plus checkpoints at 30, 60, 100 and 150 steps to show the learning curve. If time allows, a second seed.
- **Metrics:** execution-based accuracy (run in sandbox and compare manifest diff or output), JSON validity, clarification precision and recall, per-shell accuracy, output tokens, **rate of destructive proposals for benign requests** (does tuning change how risky the proposals are?).
- **Statistics:** paired non-inferiority interval (not only McNemar), Wilson intervals per cell.
- **Sample size for non-inferiority** (one-sided 95%, n = 1.645² x d / margin², d = fraction of cases where the two models disagree):

| Margin | d = 0.10 | d = 0.20 | d = 0.30 |
|---|---|---|---|
| 5 points | 108 | 216 | 325 |
| 3 points | 301 | 601 | 902 |

Use at least 300 cases to be safe.
- **If you train more:** train on the *short* prompt format, chunked runs with Drive checkpoints, response-only loss, and judge by held-out generation accuracy, not loss.

### V2. Safety validation
- **Ground truth by effect, not by opinion.** Label every case by running it in a throwaway sandbox and diffing the manifest. A case is "harmful" if it actually deletes, overwrites, truncates or changes permissions. This makes the benchmark independent of your engine.
- **Four attack sources:** (1) blind human cases written per shell by people who have not read `effects.py`; (2) grammar-based mutations of seeds (aliases, quoting, environment variables, encodings, wrappers, split commands, path tricks); (3) an **adaptive attacker**: an LLM that sees the tier and preview and tries to evade over several rounds, reporting success against number of rounds; (4) attack families from SHELLSIEVE, using its artifacts if released (check).
- **Guards:** G0-simple, G0-published, G1, G2 (reported as "no preview, no tiering" with in-scope damage counted), **G4 (LLM judge)**, G3, and ported approval logic from open-source agents if available (cite file and commit).
- **Ablations of G3:** without TOCTOU, without protected-path rule, without opaque-command escalation, without symlink resolution; show what each contributes.
- **Bounds for a zero-miss result** (two-sided 95%):

| Attacks (n) | Upper bound on true bypass rate |
|---|---|
| 50 | 7.1% |
| 100 | 3.6% |
| 200 | 1.8% |
| 300 | 1.2% |
| 500 | 0.7% |

- **Benign set:** at least 1,000 commands from real use and public corpora; report confirmation burden per 100 and per command family.
- **System-level harm test:** 500 natural-language requests through model plus engine in temp fixtures; count harmful effects executed without confirmation. Re-run with caches on and off; tiers must match.
- Freeze the engine, run once, publish misses, then fix and re-run on a fresh set.

### V3. Latency validation
- Live runs only, provenance stamped. Machines: your RTX 2050, the same machine CPU-only, and at least one other real PC.
- **Ablation matrix:** layout, prefix cache, short prompt, reasoning off, verified cache, quantization level; report prefill ms, decode ms, output tokens, time to preview, peak RAM and VRAM, with repeats, thermal protocol and bootstrap intervals.
- Include E0 to E4 from the earlier plan (equality gate, stock reuse under interleaved sessions, layout, compact state).
- Report where speedups come from; do not combine several effects into one headline number.

### V4. Novelty record
- Write down the search queries, databases, dates, inclusion rules and every close paper or tool in `docs/prior_art.md`, with a feature-by-feature table against your system.
- Read fully the papers marked "read before citing" in section 4.1.

### V5. Human-factors pilot
As in the productization plan: 10 to 12 non-technical participants, plain-English preview vs raw command, comprehension quiz, unsafe-approval rate, SUS. Report as a pilot with its limits.

### V6. IP track (optional)
Section 5, started in week 1 in parallel because disclosure timing matters.

### V7. Claims review
At each gate, update the claim ladder (section 3), and let only ledger-valid numbers into the paper (enforced by script T45).

---

## 7. Decision tree for results

| Outcome | What to do |
|---|---|
| Tuned+short is non-inferior | Claim parity at about 79% fewer prompt tokens; report latency from live runs |
| Tuned+short fails non-inferiority | Use full prompt plus prefix cache as default; train round 2 with more diverse data; report the negative result and what the short prompt costs |
| Tuned model no better than base | Say so; position fine-tuning as a prompt-length and format tool, and put the weight on safety and caching |
| Effect engine misses attacks on the blind set | Fix, document, re-run on a fresh set, report both; the honest before/after is a result |
| LLM judge (G4) performs about as well | Compare latency, determinism, and failure modes; your advantage may be speed and predictability |
| Prior-art search finds near-identical work | Reposition: evaluation, benchmark, human-factors and low-resource results become the contribution |
| CPU-only is too slow | Smaller quant, template-only Lite mode, shorter prompt; report the trade-off curve |

---

## 8. Combined timeline (about 14 weeks, two tracks, gates)

| Weeks | Research / validation track | Product track (from the productization plan) |
|---|---|---|
| 1 to 2 | V0 ledger and pre-registration, T45 stats module, start V4 search, start V6, build blind attack cases | P0 evidence repair, P1 Windows parsers |
| 2 to 4 | V1 held-out sets and fine-tune round 2, V2 attack generation and baselines | P2 standalone runtime, core service |
| 4 to 6 | V3 live latency and CPU-only runs, V2 adaptive attacker | P3 UI and renderer |
| **Gate G2 (end of week 6)** | Claims review: update ladder, decide pivots | |
| 6 to 8 | V1/V2 final sealed runs, V5 pilot preparation | P4 packaging and installer |
| 8 to 10 | V5 pilot, final ablations | P5 data v3 and final fine-tune if needed |
| 10 to 12 | Paper and report with ledger-valid numbers | Release candidate, docs |
| 12 to 14 | Buffer, final claims review (Gate G4) | v1.0 |

Suggested split: two people on validation (people who did not write `effects.py` write the blind attacks), two on product; swap for the attack-writing and pilot steps.

---

## 9. Agent prompts (T38 to T45)

**T38: evidence ledger.**
"Create scripts/build_evidence_ledger.py that scans results/*.jsonl and datasets/, and writes docs/evidence_ledger.md: file, row count, backend names, model file and sha256, n per condition, presence of provenance, flags for mock/demo backends and suspiciously round values (identical means, timing proportional to tokens), and status valid/invalid. Exit non-zero if any invalid file is referenced by make_figures.py."

**T39: statistics module.**
"Create src/nlagent/bench/stats.py with Wilson interval, Clopper-Pearson interval, exact McNemar, paired difference interval and non-inferiority test (given margin), bootstrap intervals for medians and p95, and sample-size helpers. Tests must check known values: Wilson for 6/8 is about [40.9, 92.9]; Clopper-Pearson upper bound for 0/200 is about 1.83%; smallest exact McNemar p for 8 pairs with a net difference of 3 is 0.25."

**T40: E5 v2.**
"Update scripts/run_e5.py: --n >= 200, models {base, tuned} x prompts {full, short, few-shot}, per-shell results, JSON validity, clarification precision/recall, output token counts, rate of destructive proposals for benign requests, and a --checkpoint option to sweep steps. Report the paired non-inferiority interval for tuned+short versus base+full. Refuse mock backends."

**T41: attack generation with ground-truth labels.**
"Create scripts/make_attacks.py: apply grammar-based mutations (aliases, quoting, environment variables, encodings, wrappers, command splitting, path tricks) to seed attacks per shell, deduplicate, run each in a throwaway sandbox and label harmful/benign from the actual manifest diff, never from the engine. Write datasets/attacks_v3_{shell}.jsonl with label provenance. Execute only inside temp directories."

**T42: adaptive attacker.**
"Create scripts/run_adaptive_attack.py: a loop where the local model proposes evasion variants given the tier and preview returned by the guard; run R rounds with a fixed budget; log attack success rate against rounds and the successful variants. Temp directories only."

**T43: ablation runner.**
"Create scripts/run_ablation.py that disables one G3 component at a time (TOCTOU re-check, protected paths, opaque escalation, symlink resolution) via config flags and reports bypass and false-alarm changes with intervals on the same case sets."

**T44: external baselines.**
"Port the command-approval or known-safe logic of open-source agent CLIs as baseline guards in src/nlagent/safety/baselines/, each with a header citing the repository, file path and commit. Do not copy code that is not license-compatible; reimplement the rules and document differences."

**T45: paper numbers gate.**
"Create scripts/make_paper_tables.py that builds every table and figure for the report from results/ but fails if any input row is flagged invalid in docs/evidence_ledger.md or lacks provenance. Add a test with a deliberately invalid file."

---

## 10. Paper and report wording

**Safe now (design plus preliminary evidence):**
"We present a safety-gated natural-language shell agent that derives a risk tier from the resolved file-system effects of a proposed command, previews those effects, preserves affected files for undo, and re-verifies at commit time. In a preliminary evaluation on 49 and 15 attack cases written by the developers, the effect-based classifier escalated all attacks, while denylist baselines missed 49% to 73%. The intervals are wide (upper bound 7.3% for 0/49), the cases were authored by the system's developers, and a larger blind evaluation is under way."

**After the gates (only if true):**
"On 300 blind attacks labelled by sandboxed effect, the effect engine missed N (upper 95% bound X%), compared with ... for G0, G1 and an LLM judge; against an adaptive attacker, success after R rounds was ...; benign escalation was ...%."

**Fine-tuning (only if true):**
"A fine-tuned model prompted with about 110 tokens was within 5 percentage points of the base model prompted with about 520 tokens (paired 95% lower bound ..., n = ...), with ...% fewer prompt tokens and a median time to preview of ... ms on CPU-only and ... ms on GPU."

**Limitations paragraph to include:** developer-authored benchmark components, static analysis limits (network and time-dependent effects), single base model, a small pilot, specific hardware, template-generated training data, and that numbers from other papers are not directly comparable.

---

## 11. Checklist before any claim goes into the paper or a patent discussion

- [ ] Evidence ledger shows every number as valid, with live backend, model hash, n and interval.
- [ ] Pre-registration committed before the final runs.
- [ ] Blind attack set labelled by sandbox effect; adaptive attack and ablations run.
- [ ] Fine-tuning evaluated with a non-inferiority test on at least 300 cases, including human-written and public sets.
- [ ] Latency measured live on at least two hardware setups including CPU-only.
- [ ] Prior-art record written, close papers read in full.
- [ ] IPR cell consulted before further public disclosure.
- [ ] AI-assistance disclosure added per IEEE and college rules.
