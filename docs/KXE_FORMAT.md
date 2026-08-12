# KXE v0 compatibility notes

This document describes the independently implemented KXE reader used by the
Eclipse PC/Switch project. It is written for contributors who need to work on
the native integration without first learning the Kuribo codebase.

## Why KXE matters

Eclipse's `main.dol` is only the base game. At runtime Kuribo loads four KXE
modules in this order:

1. `BetterSunshineEngine.kxe` — the shared framework and dependency root.
2. `BetterSunshineMoveset.kxe` — imports 15 BSE functions.
3. `MirrorMode.kxe` — imports 2 BSE functions.
4. `SuperMarioEclipse.kxe` — imports 66 BSE/runtime functions.

The native recompilation therefore has to preserve both the module code and
the initialization/import behavior. Recompiling only `main.dol` cannot produce
a playable Eclipse build.

## Provenance boundary

The parser in `tools/kxe/format.py` is new project code. Format facts and
observable behavior were cross-checked against the public
[Kuribo repository](https://github.com/DotKuribo/Kuribo), while PowerPC branch
and address behavior was checked against IBM's Gekko/PowerPC manuals in the
locally held GameCube SDK documentation. No Kuribo loader or converter source
was copied. This is especially important because the current Kuribo repository
does not declare a project license.

The user's game image and KXE files are private validation inputs. They remain
under ignored `ref/` paths and must never be committed, uploaded, or placed in
a release.

## Container layout

All integers are big-endian. Version 0 begins with this 132-byte logical
header; observed files pad the payload start to 160 bytes.

| Offset | Size | Meaning |
|---:|---:|---|
| `0x00` | 4 | ASCII magic `KXER` |
| `0x04` | 2 | format version, currently `0` |
| `0x06` | 2 | required kernel ABI version, currently `0` |
| `0x08` | 4 | declared file size |
| `0x0c` | 4 | container flags |
| `0x10` | 96 | six 16-byte section descriptors |
| `0x70` | 4 | entry-point offset from the code base |
| `0x74` | 16 | embedded-file descriptor |

The six descriptors are code, data, BSS, relocations, imports, and exports.
Each contains file offset, file size, CRC32, one-byte alignment, one-byte flags,
and two reserved bytes. `0xffffffff` in both offset and size means absent. The
current Eclipse modules are code-only: data, BSS, and exports are absent.

The compression flag exists in the format, but the prototype rejects it until
an independently verified decoder is added.

### Checksum caveat

The code CRC is diagnostic, not an unconditional validity check. The public
converter records it before applying static absolute-address relocations, so
the bytes finally written to disk can legitimately have a different CRC. All
four shipped Eclipse modules exhibit this behavior. Strict checking remains
available to tests through `parse_kxe(..., require_code_crc=True)`.

## Relocations

Each relocation record is 16 bytes:

| Field | Size | Meaning |
|---|---:|---|
| type | 1 | PowerPC EABI relocation number |
| affected section | 1 | section containing the word/halfword to patch |
| source section | 1 | where the target originates |
| reserved | 1 | must be zero |
| affected offset | 4 | patch position in the affected section |
| source offset | 4 | target offset or import-table index |
| addend | 4 | unsigned 32-bit addend |

Source section `0` means the module's code base, `0xfd` means an absolute raw
address, and `0xff` means an imported procedure. Import records point to UTF-8
names and also carry CRC32 identifiers.

The shipped Eclipse set uses only relocation types `1`, `4`, `5`, `6`, and
`10` (`ADDR32`, `ADDR16_LO`, `ADDR16_HI`, `ADDR16_HA`, and `REL24`). It does
not use Kuribo's type-109 SDA trampoline extension. The parser understands the
standard v0 type set but intentionally rejects type 109 during application
until equivalent trampoline behavior is required and tested.

Relative branches preserve the opcode/control bits, replace only the LI or BD
field, and require four-byte alignment. The implementation rejects targets
outside the signed field range instead of silently wrapping them.

## Current private-input audit

These counts identify the supported Eclipse 1.1.0 profile without publishing
game data:

| Module | Code bytes | Relocations | Imports | Entry offset |
|---|---:|---:|---:|---:|
| Better Sunshine Engine | 467,488 | 8,462 | 0 | `0xe314` |
| Better Sunshine Moveset | 27,904 | 933 | 15 | `0x4c` |
| Mirror Mode | 10,176 | 182 | 2 | `0x4c` |
| Super Mario Eclipse | 241,952 | 9,222 | 66 | `0xda10` |

## Native conversion plan

The current prototype performs the lowest-risk conversion in stages:

1. Parse and bounds-check every KXE without modifying it.
2. Choose a deterministic guest address and apply its internal/raw-address
   relocations.
3. Resolve named imports only after a compatible BSE export map exists.
4. Wrap relocated code in a minimal GameCube DOL and feed it to DolRecomp.
5. Merge each generated code range and self-modifying-code range into the
   existing `GMSE04` native module.
6. Recreate Kuribo's module context and call each entry point in dependency
   order, including BSE constructor/export registration before dependents.
7. Validate runtime patch writes, menus, stage loading, saving, and shutdown.

Stage 4 now succeeds for Better Sunshine Engine at guest base `0x81600000`.
The current prototype combines it with Eclipse's DOL and a high-memory guest
shim. The shim runs first at `0x817f0000`, lowers ArenaHi to `0x81600000` before
Sunshine clears its heap, and then jumps to the original entry point. This
reserves roughly the same memory that dynamic Kuribo modules would consume.

At the original Kuribo loader hook (`0x802a744c`), the shim calls BSE's real
prologue at `0x8160e314`, records its exported name/address pairs, and resumes
the game at `0x802a7450`. The dynamic kernel loader is therefore bypassed and
the supported path contains one BSE initialization call.

The combined Windows module passes ModernGekko ABI inspection: ABI 3, four
code ranges, 1,620 self-modifying-code ranges, and 251 native chunks. A bounded
30-second headless diagnostic stayed alive without a module, ABI, or immediate
boot error. That is a bring-up result, not gameplay acceptance.

After the normal Eclipse Windows preparation, reproduce the conversion with:

```powershell
.\scripts\prepare-eclipse-kxe.ps1 -BuildNativeModule
```

Build the integrated DOL+BSE prototype with:

```powershell
.\scripts\prepare-eclipse-combined.ps1
```

The first MSVC build can take several minutes because some generated C chunks
are very large. All outputs are ignored private artifacts.

The export boundary is now solved without a live-memory dump. A relocation-
aware PowerPC constant-propagation pass reconstructs BSE's calls to
`register_procedure`, including the compiler's reused-register form. It
recovers 139 exact exports and resolves all 68 unique imports needed by the
three consumers. The combined image places BSE at `0x81600000`, Moveset at
`0x81673000`, Mirror Mode at `0x8167a000`, Eclipse at `0x8167d000`, and the
lifecycle shim at `0x817f0000`. The shim also implements runtime name lookup.

All four module prologues are now called in dependency order, and the combined
DLL passes ABI inspection with 269 chunks. The remaining boundary is runtime
acceptance: visible rendering, menus, gameplay, patches, saves, and shutdown.
