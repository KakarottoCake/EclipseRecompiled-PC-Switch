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

None of the private extraction, generated game code, or compiled game module
is committed or distributable from this repository.

## Why it is not playable yet

Eclipse is not only a modified `main.dol`. It loads Kuribo's kernel and four
PowerPC `.kxe` modules at runtime. Those modules contain Better Sunshine
Engine, the moveset, mirror mode, and Eclipse itself. The Windows host and DOL
module are ready, but the Kuribo/KXE layer has not yet been converted or
reimplemented for the native runtime.

The next concrete milestone is an independently implemented KXE reader and
link plan covering sections, imports, relocations, initialization order, and
patch hooks. A diagnostic DOL-only launch may fail; it is not presented as a
playable build.

## Reproducible Windows command

Install Visual Studio 2022 Build Tools with Desktop C++, Git for Windows,
CMake 3.31+, Ninja, and Python 3. Then run from PowerShell:

```powershell
.\scripts\build-eclipse-windows.ps1 `
  -DiscImage "D:\path\to\your\Super Mario Eclipse 1.1.0.iso"
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
