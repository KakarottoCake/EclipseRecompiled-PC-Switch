# Session handoff — native-backend perf + lockstep tooling (2026-08-17)

Handoff for another agent picking up the Eclipse native-recomp performance work.
Standing goal: **stable 30/60 fps on the native recompiled backend** (DolRecomp
static PPC→LLVM→x86-64), not Dolphin's JIT.

The working tree on this machine already contains everything below. A *fresh
clone does not* — see "Repo reality" at the bottom.

---

## What shipped this session (measured, live in the chassis binary)

### 1. Word-patch prefilter — +2.73 points, 6/6 pairs
`StaticRecompCore::IsRuntimePatchAddress` binary-searched 670 16-byte records
per dispatch (~10 unpredictable branches) — the single hottest chassis site
(+0x00209E65, ~2% of all runtime). Replaced with a **1 KB / 8192-bit hashed
prefilter + min/max range test** ahead of the search; both conservative, so the
exact answer is unchanged.

- `bit = ((addr >> 2) * 2654435761u) >> 19`
- Offline-validated vs the shipped 670-entry JSON: **0 false negatives**, 7.82%
  occupancy → only 7.83% of queries reach the search.
- Range test alone rejects Eclipse's injected code (0x8043A000,
  0x804D0000–0x8055F000) on one compare — patches span only 0x80005624..0x8031E7C8.
- **Chassis A/B, one fixed PGO module, 6 pairs: +2.73 mean, 6/6 favour it**,
  90.83 → 93.57. Two runs at 100.2% / 99.9%.
- Files: `StaticRecompCore.h` (members + `PatchFilterBit`), `StaticRecompCore_SMC.cpp`
  (`IsRuntimePatchAddress` tests + `BuildRuntimePatchFilter`), `StaticRecompCore.cpp`
  (call `BuildRuntimePatchFilter()` where `m_module` is set).
- Profile after: +0x00209E65 dropped out of the chassis top-15.

### 2. Lockstep step-cap artefact — fixed at default settings
The lockstep verifier reported a **45.4% divergence rate that is ~6× inflated**
by two harness artefacts. Fixed one in-tree:
- Default `m_ls_step_cap` **512 → 20000** (`StaticRecompLockstep.h`).
- A run that exhausts the cap now increments a separate `m_ls_cap_hits` counter
  and reports nothing, instead of emitting a bogus CTRLFLOW divergence
  (`StaticRecompLockstep_Check.cpp`); counter surfaced in the summary line
  (`StaticRecompLockstep.cpp`).
- Default run went `checks=9461 reports=780 cap_hits=2` — **8.2%**, where the
  same config reported 45.4% that morning.

### 3. freeze-probe `-Uncapped` switch (WRITTEN, EFFECT UNVERIFIED)
`scripts/freeze-probe.ps1` gained `-Uncapped`, which writes
`EmulationSpeed = 0.0` into the *copied* user dir's `Config/Dolphin.ini`. The ini
lands correctly and native boot is unaffected, **but its effect is unproven** —
the native backend is CPU-bound below 100%, so an uncapped native run looks
identical whether the throttle is off or on. The verifier (Dolphin JIT, which
would exceed 100%) can't boot — see task #35. Do not trust `-Uncapped` until
proven, e.g. by reading the emulator's runtime throttle state directly.

---

## Reverted this session (do NOT re-port)
Upstream RecompCore `d3c97600ba` lockstep loop-exit fix: **zero benefit here**
(`undercharges=0`; `-FunctionRanges` ends ranges at function boundaries, not
inlined loop headers) **and +80 false CTRLFLOW reports**. A comment at the loop
in `StaticRecompLockstep_Check.cpp` records why. Its value was the *investigation*
(the artefact findings above), not the code.

---

## Key finding: the module is ~30× more correct than the tooling said
Divergence rate, peeling artefacts off in order:

| stage | reports / checks | rate |
|---|---|---|
| as believed AM | 4301 / 9465 | 45.4% |
| − dead-volatile reporting | 1126 / 9396 | 12.0% |
| − step-cap CTRLFLOW (fixed) | 780 / 9461 | 8.2% |
| − FPRF/FI/FR modelling gap | ~137 | ~1.4% |

**82% of the real remainder is a benign FPSCR flag gap** (FPCC + FI/FR updated on
every FP op by hardware, only on `fcmp*` by us). Confirmed benign: `mffs=3,
mcrfs=0` in the whole guest binary, and **zero FP exception bits** in 643
mismatches. The ~137 non-FPSCR reports are the first readable correctness signal
this project has had.

---

## Open tasks (see the project task list + memory files)
- **#34** Mask FPRF/FI/FR out of the lockstep FPSCR comparison (verifier change,
  NOT codegen — confirmed benign). Cheapest; makes lockstep readable.
- **#32** Teach lockstep to skip registers the module declared dead
  (`-DeadVolatiles`). This is an **ABI change** — `StaticRecompABI.h` carries no
  liveness metadata. Blocks running lockstep against the *shipping* config.
- **#35** Dolphin JIT reference path fails to boot (0/4 runs, incl. without
  `-Uncapped`). Matters: it's the yardstick for the "~2× gap" in task #6. Start
  at `out/performance-logs/probe-jit-control-*/stderr.log`.
- **#33** Identify new top chassis site `+0x001DB660` (1.14%) + the `+0x001640Bx`
  pair. Method that cracked +0x00209E65: disassemble, read struct field offsets,
  match against `StaticRecompABI.h`.
- **#31** Real-function-call model (callees inherit register state) — now
  evaluable once #32 gives trustworthy divergence counts.
- **Owed:** clean post-prefilter profile at *normal speed* (the one taken this
  session was at 70.9%, not comparable to the earlier 93.6% profile).

Memory files with full detail (in the agent's memory store, not this repo):
`eclipse-word-patch-prefilter`, `eclipse-lockstep-artefacts`,
`eclipse-codegen-pgo`, `recompcore-fork-state`, `eclipse-smc-word-patches`.

---

## Measurement gotchas that bit me this session
- `freeze-probe.ps1` reports via `Write-Host` — capture with `6>&1` or every
  speed parses as `NaN` and the A/B prints a confident wrong verdict.
- Chassis-only changes need **no PGO retraining**; both A/B arms use the same
  PGO module. Build with `--target moderngekko-run` (~2 min); do NOT run
  `build-eclipse-windows.ps1` (it regenerates the module first).
- This host has a **bimodal low band** (~81-85% both arms) that costs ~13 points
  when it hits; it has muddied several experiments. Largest single source of
  measurement variance right now.
- +2.73 is a **floor** only if runs were clipping at 100%; the uncapped ~95%
  runs suggest they weren't, i.e. +2.73 is likely the real delta. Unresolved
  until `-Uncapped` is verified (task #35 blocks it).

---

## Repo reality (why upstreaming is a follow-up, not done here)
Three nested repos, different owners:
- Top-level `KakarottoCake/EclipseRecompiled-PC-Switch` (owned) — Eclipse scripts,
  patches, word-patch JSON.
- `vendor/dolphin` → `ExpansionPak/RecompCore` — **where this session's C++ lives**.
  Committed HEAD is 85 commits *behind* that upstream.
- parent `ref/ModernGekko` → `ExpansionPak/ModernGekko`.

Forks created this session: `KakarottoCake/RecompCore`, `KakarottoCake/ModernGekko`.

**Blocker for clean upstream PRs:** this session's additions are entangled with a
large *pre-existing uncommitted* fork delta in the same files (e.g.
`StaticRecompCore_SMC.cpp` has 212 uncommitted insertions; ~40 are this session's).
There is no clean git seam between "this session" and "the broader Eclipse fork
delta." A mergeable PR of just-our-additions requires reconstructing my hunks
onto ExpansionPak's current tree and resolving against the 85-commit divergence —
real work that must build/test against their tree before opening a public PR.
`handoff/session-recompcore-changes.patch` is the working-tree diff of the 6
files I touched (mine + surrounding pre-existing context); the distinctive
markers of *my* hunks are `PatchFilterBit`, `BuildRuntimePatchFilter`,
`m_patch_filter`, `PATCH_FILTER_BITS`, `cap_hits`.
