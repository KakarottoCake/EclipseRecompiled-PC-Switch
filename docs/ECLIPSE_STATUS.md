# Eclipse bring-up status

Last verified: August 12, 2026.

## What works now

- The exact Super Mario Eclipse 1.1.0 `GMSE04` image is validated locally by
  disc and DOL SHA-256 before use.
- DolRecomp builds natively with Visual Studio on Windows.
- The image is safely extracted to an ignored private directory.
- The included `marioUS.MAP` resolves 12,903 executable symbols.
- DolRecomp converts the modified `main.dol` into 221 native code chunks.
- The resulting `gGMSE04_recomp.dll` builds with MSVC and passes ModernGekko's
  module ABI inspection: ABI 3, entry point `0x8000522c`, 2 code ranges,
  138 self-modifying-code ranges, and 221 chunk ranges.
- The branded Windows host builds: `EclipseRecompiled.exe`,
  `EclipseRecompiled-run.exe`, and `moderngekko-port.exe`.
- The Windows host includes SDL controller support and Dolphin's direct
  GameCube-adapter support.
- The independent KXE v0 reader validates all four shipped Eclipse modules,
  including 18,799 relocations and 83 named imports in total.
- Better Sunshine Engine relocates at the experimental `0x81700000` fixture
  and DolRecomp converts it into 29 native C chunks.
- Those BSE chunks compile into `gBSE000_recomp.dll` and pass the runtime ABI
  inspector: ABI 3, entry `0x8170e314`, 1 code range, 938 SMC ranges, and 29
  chunk ranges. This proves the first KXE can enter the full native toolchain;
  it is not integrated into the game module yet.
- A second prototype now combines the Eclipse DOL, relocated BSE, and a guest
  lifecycle shim into one `GMSE04` DLL: ABI 3, 4 code ranges, 1,620 SMC ranges,
  and 251 chunks. The shim reserves high memory before OS initialization,
  bypasses the original dynamic Kuribo loader, invokes BSE once at the original
  hook point, and captures BSE's runtime exports.
- A bounded 30-second headless launch remained active with no immediate module,
  ABI, or boot failure. Rendering and gameplay still need explicit acceptance;
  this is not a playable milestone.
- Relocation-aware PowerPC analysis now recovers 139 exact BSE runtime exports
  from the shipped binary. All 68 unique imports required by Moveset, Mirror
  Mode, and Eclipse resolve without a guessed address.
- The current combined prototype relocates and initializes all four KXEs in
  dependency order. Its Windows DLL passes ABI inspection with entry
  `0x817f0000`, 4 code ranges, 2,843 SMC ranges, and 269 chunks. The hardened
  build, including runtime name lookup, also remained active for a bounded
  30-second headless diagnostic with no immediate error.

None of the private extraction, generated game code, or compiled game module
is committed or distributable from this repository.

## Why it is not playable yet

Eclipse is not only a modified `main.dol`. It loads Kuribo's kernel and four
PowerPC `.kxe` modules at runtime. Those modules contain Better Sunshine
Engine, the moveset, mirror mode, and Eclipse itself. The prototype now
statically replaces that loader and links all four, but a headless process
remaining alive does not prove that their runtime patches, rendering, or game
logic are correct.

The next concrete milestone is visible Windows acceptance: confirm rendering,
reach Eclipse's menus, enter gameplay, and diagnose any runtime patch or
unsupported-instruction failures encountered along that path.
See [the KXE compatibility notes](KXE_FORMAT.md) for the verified format,
conversion path, and remaining runtime boundary. A diagnostic DOL-only launch
may fail; it is not presented as a playable build.

## Reproducible Windows command

Install Visual Studio 2022 Build Tools with Desktop C++, Git for Windows,
CMake 3.31+, Ninja, and Python 3. Then run from PowerShell:

```powershell
.\scripts\build-eclipse-windows.ps1 `
  -DiscImage "D:\path\to\your\Super Mario Eclipse 1.1.0.iso"
.\scripts\prepare-eclipse-combined.ps1
```

The first run downloads pinned open-source dependencies and takes several
minutes. It never downloads a game image. Later runs reuse the ignored local
checkouts and build outputs.

## Known upstream test issues

DolRecomp's actual Windows tools and Eclipse generation path pass. Its current
CTest suite reports 12 of 14 tests passing on this machine. The two remaining
failures are test-harness drift rather than Eclipse code-generation failures:

- `disc_extract` still invokes a removed `--native-only` option.
- `codegen_compile` starts a nested configure without preserving the selected
  native Ninja executable when devkitPro is also installed.

These are suitable small upstream contributions after this checkpoint.
