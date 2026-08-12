# Super Mario Eclipse PC and Nintendo Switch Port Plan

Last updated: August 12, 2026.

## Decision

Build Eclipse on the proven SunPad stack:

- **CPU recompilation:** DolRecomp
- **GameCube compatibility runtime:** ModernGekko with its Dolphin/RecompCore
  foundation
- **First product target:** Windows x86-64
- **Secondary desktop target:** Linux x86-64
- **Later device target:** Nintendo Switch homebrew, ARM64/libnx
- **Game target:** Super Mario Eclipse `GMSE04`, initially version 1.1.0

The older GameCubeRecompiled/Rust experiment is preserved separately for
research and small upstream contributions, but it is not the product runtime.
SunPad already demonstrates playable Super Mario Sunshine; completing another
graphics, audio, input, SDK, and operating-system runtime would duplicate the
largest and riskiest part of the work.

## Confirmed Eclipse disc facts

The repository's read-only inspector was run against the user's local Super
Mario Eclipse 1.1.0 image. No extracted content was committed.

| Property | Observed value |
|---|---|
| Disc ID | `GMSE04` |
| Disc/revision | disc 0, revision 0 |
| Title | `Super Mario Eclipse` |
| Raw image size | 1,459,978,240 bytes |
| Disc SHA-256 | `d47de756d57511c427c1d500858f7eee5bbcb1a8e5e40c97758d18cb2f2bed6f` |
| `main.dol` size | 4,128,928 bytes |
| `main.dol` SHA-256 | `5a146d7d8b2c8244a6188beb1f7c9b738b13897eb0cdacc02283a8a810cac134` |
| Files/directories | 473 files, 11 directories |
| REL modules | none found in the disc filesystem |
| Symbol map | `marioUS.MAP`, 7,997,342 bytes |

The dynamic Eclipse layer consists of:

| Disc path | Size |
|---|---:|
| `Kuribo!/Mods/BetterSunshineEngine.kxe` | 603,040 bytes |
| `Kuribo!/Mods/BetterSunshineMoveset.kxe` | 43,936 bytes |
| `Kuribo!/Mods/MirrorMode.kxe` | 13,376 bytes |
| `Kuribo!/Mods/SuperMarioEclipse.kxe` | 393,472 bytes |
| `Kuribo!/System/KuriboKernel.bin` | 21,517 bytes |

Every KXE begins with the `KXER` signature. These are real loadable code
modules, not ordinary asset replacements. A recompilation of `main.dol` alone
will therefore not be a complete Eclipse port.

The inspector also validated the version-0 KXE headers, section tables,
relocations, and import tables without extracting their code:

| Module | Code bytes | Relocations | Imports | Relocation types |
|---|---:|---:|---:|---|
| Better Sunshine Engine | 467,488 | 8,462 | 0 | 1, 4, 5, 6, 10 |
| Better Sunshine Moveset | 27,904 | 933 | 15 | 1, 4, 6, 10 |
| Mirror Mode | 10,176 | 182 | 2 | 1, 4, 5, 6, 10 |
| Super Mario Eclipse | 241,952 | 9,222 | 66 | 1, 4, 5, 6, 10 |

Those types correspond to standard PowerPC absolute/halfword/branch
relocations in Kuribo's public format definitions. None of these four files
uses Kuribo's unusual relocation type 109. This makes ahead-of-time conversion
materially more credible: all shipped modules have one code section, ordinary
relocations, and explicit imports; Better Sunshine Engine is the dependency
root and the other modules import its API.

Re-run the audit locally with:

```powershell
.\scripts\inspect-gamecube-disc.ps1 `
  -DiscImage "D:\path\to\Super Mario Eclipse.iso"
```

Use `-IncludeDiscHash` for exact-version validation and `-Json` for structured
output. The script reads the header, DOL layout, and filesystem table directly;
it does not extract or alter the image.

## Architecture

```text
User-supplied Eclipse GMSE04 image
                |
                v
     validate and extract locally
                |
       +--------+---------+
       |                  |
       v                  v
  modified main.dol   four Kuribo KXE modules
       |                  |
       v                  v
 DolRecomp native C   KXE parser/linker or static conversion
       |                  |
       +--------+---------+
                |
       ModernGekko game module
                |
       +--------+----------+
       |                   |
       v                   v
 Windows/Linux host   Switch libnx host
 DLL/SO module        statically linked ARM64
```

## Milestone 0: preserve reproducibility and legal boundaries

Acceptance gates:

- SunPad remains the recorded GitHub fork parent.
- All public dependency revisions and local patches are pinned and reviewable.
- ISO/GCM files, extracted assets, generated code, modules, saves, logs, and
  signing material remain ignored.
- Build and package audits fail if proprietary material is accidentally staged.
- Public documentation distinguishes source inspection from tested behavior.

## Milestone 1: known-good Windows retail baseline

Before debugging Eclipse, prove that the inherited stack works on the actual
Windows toolchain and graphics drivers.

Tasks:

1. Bootstrap the pinned ModernGekko, Dolphin/RecompCore, DolRecomp, and template
   checkouts.
2. Build DolRecomp, the ModernGekko port tool, runner, and launcher on Windows.
3. Prepare a user-supplied supported retail `GMSE01` image locally.
4. Generate the native Windows module.
5. Reach the same title/gameplay path documented by SunPad.
6. Record compiler, renderer, audio, input, and performance evidence.

Acceptance gate: retail Sunshine reaches controllable gameplay on Windows from
a clean local preparation, with no PowerPC JIT required for the normal path.

## Milestone 2: Eclipse DOL proof

Tasks:

1. Add a Windows preparation script that validates the exact `GMSE04` image.
2. Extract to a unique staging directory and activate atomically.
3. Feed the included `marioUS.MAP` to DolRecomp where its format is accepted.
4. Generate the modified `main.dol` module for Windows.
5. Launch with KXE loading deliberately disabled and capture the first failure.
6. Compare dispatch, memory, DVD, and HLE behavior against the retail baseline.

Acceptance gate: the Eclipse DOL module validates and enters the shared runtime;
any stop is diagnosed as a specific missing KXE/runtime behavior rather than a
module-build or packaging failure.

## Milestone 3: Kuribo and Better Sunshine Engine

This is the critical Eclipse milestone.

First research the public Kuribo and Better Sunshine Engine sources and map the
KXE header, sections, relocations, imports, exports, constructors, and patch
operations. Do not reverse proprietary Nintendo code into the repository.

Kuribo's public repository currently declares no license in GitHub metadata.
Treat it as a format/behavior reference only: do not copy its implementation
into this GPL project unless the copyright holders add a compatible license or
grant permission. Implement the small KXE reader independently from documented
binary facts and tests. Better Sunshine Engine itself declares GPL-3.0 and can
be used under its license with attribution.

Evaluate these approaches in order:

1. **Build from public mod source for the host.** Best when Eclipse-compatible
   source revisions and build definitions are available.
2. **Convert KXE modules ahead of time.** Parse their PowerPC sections and
   relocations, then feed code through DolRecomp and link the resulting native
   modules into the game package.
3. **Implement runtime KXE loading.** Use only if module discovery or dynamic
   behavior prevents static integration.

The host must reproduce Kuribo's observable ABI:

- symbol/import resolution against Sunshine and other modules;
- section allocation and relocations;
- module initialization order;
- function patches and hooks;
- data/bss lifetime;
- inter-module dependencies;
- safe failure diagnostics and exact module fingerprints.

Acceptance gate: Better Sunshine Engine, Better Sunshine Moveset, Mirror Mode,
and Super Mario Eclipse initialize in the expected order, with deterministic
address/symbol resolution and no interpreter-only module execution hidden in
the supported configuration.

## Milestone 4: playable PC alpha

Tasks:

- reach menus, file selection, Delfino Plaza, and representative Eclipse areas;
- preserve saves across upgrades and failed imports;
- support keyboard plus normalized Xbox, PlayStation, and Switch controllers;
- directly detect Nintendo/Mayflash GameCube USB adapters where supported;
- preserve analog trigger pressure for FLUDD;
- provide controller selection, remapping, dead zones, rumble, fullscreen,
  aspect ratio, and render scale through an approachable launcher;
- retain ModernGekko's code-mod packages and add safe loose-file overrides;
- add privacy-reviewed diagnostic logs.

Acceptance gate: a clean Windows build can import the supported Eclipse image
and complete an extended controller-driven play session without developer-only
manual file placement.

Linux follows after the Windows alpha. Linux support must share the generated
game/runtime behavior rather than become an independent port.

## Milestone 5: Nintendo Switch technical proof

The Switch target is homebrew for user-controlled hardware using libnx. It is
not an official Nintendo SDK/eShop target.

Architecture decisions:

- cross-compile the generated module for ARMv8-A with devkitA64;
- disable desktop dynamic-module assumptions and register the game module in
  process, preferably by static linking;
- use interpreter fallback only as an explicit diagnostic mode;
- use libnx for app lifecycle, SD filesystem, HID, vibration, audio, timing,
  threading, and suspend/resume;
- evaluate NXVK/Vulkan against the Dolphin-derived renderer first;
- keep a deko3d-specific renderer adaptation as the fallback if NXVK cannot
  meet correctness or performance requirements;
- package only an `.nro`, metadata/icon, configuration, and open-source code;
  the user supplies and imports Eclipse data on the SD card.

First proof acceptance gate:

- `.nro` launches from the Homebrew Menu with full memory access;
- exact generated ARM64 module is registered and validated;
- coherent frames reach the display;
- Joy-Con or Pro Controller drives gameplay input;
- audible continuous output is produced;
- saves write to and reload from the SD card;
- suspend/resume either works or exits safely without corrupting data.

## Milestone 6: Switch alpha and release hardening

Tasks:

- profile CPU, GPU, memory, module size, cold load, and shader compilation;
- validate handheld and docked resolutions;
- add GameCube-style mappings and optional adapter research;
- test multiple firmware/libnx combinations and rebuild with current libnx;
- validate long play sessions, area transitions, saves, sleep/wake, controllers,
  and clean shutdown;
- audit the final archive for game data, local paths, logs, and signing material;
- publish source, exact dependency revisions, build instructions, checksums,
  limitations, and attribution with every binary release.

## Immediate work queue

1. Finish the pinned dependency bootstrap on Windows.
2. Make the ModernGekko desktop tools configure and compile with the available
   Windows toolchain.
3. Add a safe Windows disc preparation flow derived from SunPad's staging and
   validation rules.
4. Generate the Eclipse DOL module.
5. Locate the public Kuribo loader/format definitions and document the exact
   KXE ABI.
6. Decide source rebuild versus ahead-of-time KXE conversion using evidence
   from the four shipped modules.

Do not begin the Switch renderer port before the Eclipse module stack reaches
the Windows runtime. Doing so would combine game, module-loader, renderer,
audio, input, and platform failures into one undebuggable target.
