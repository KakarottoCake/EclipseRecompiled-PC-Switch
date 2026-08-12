# Patch Snapshots

This fork carries complete, reviewable snapshots of all required changes to
its ignored upstream trees:

| Patch | Applies to | Contents |
|---|---|---|
| `ModernGekko/0001-sunpad-apple-runtime.patch` | Pinned ModernGekko root | Apple frontend/runtime integration, macOS Metal defaults, iOS platform and build wiring, and the SunPad-owned files required by the Apple workflows |
| `ModernGekko/0002-eclipse-windows-runtime.patch` | Patched ModernGekko root | Prevents MSVC from forcing Dolphin's C++ precompiled header onto GXRuntime's C exception source |
| `ModernGekko/0003-eclipse-direct-dol.patch` | Patched ModernGekko root | Adds an explicit direct-DOL launch option and attaches the extracted GameCube volume to that executable |
| `ModernGekko-dolphin/0001-sunpad-ios-runtime.patch` | Pinned `ModernGekko/vendor/dolphin` | Complete Dolphin-derived iOS/runtime delta, including Metal/platform guards and stubs, no-JIT/software-loader behavior, iOS audio integration, StaticRecomp timebase/TL/TU fixes, and iOS backend/link fixes |
| `ModernGekko-dolphin/0002-eclipse-windows-runtime.patch` | Patched `ModernGekko/vendor/dolphin` | Small MSVC portability fix for the GXRuntime lockstep-journal export used while compiling native Windows game modules |
| `ModernGekko-dolphin/0003-eclipse-direct-dol.patch` | Patched `ModernGekko/vendor/dolphin` | Carries disc ID/FST records into direct-DOL boots and makes the EXI/memory-card startup path usable without the IPL/apploader |
| `ModernGekko-dolphin/0004-eclipse-smc-fallback.patch` | Patched `ModernGekko/vendor/dolphin` | Keeps runtime-modified static-recomp chunks in Dolphin's interpreter instead of letting the fallback JIT execute stale native blocks |

These replace the earlier partial patch series. Required CoreAudio,
mixer, platform-stub, frontend, build, and direct-DOL changes are no longer
described as unrepresented local edits.

Do not apply these snapshots by hand to an arbitrary checkout. From the
repository root, run:

```sh
./scripts/bootstrap-dependencies.sh
```

The bootstrap script checks out the exact revisions recorded in
[DEPENDENCIES.md](../docs/DEPENDENCIES.md), verifies the vendored Dolphin
revision, and applies each patch once. It accepts a patch that is already
fully applied and stops if a checkout is on an unexpected commit or either
snapshot does not apply cleanly.

The snapshots contain generic Apple/runtime integration for SunPad's current
`GMSE01` development path, the isolated Windows portability delta needed by
the Eclipse PC module build, and the direct-DOL/extracted-volume bridge used by
Eclipse. Direct-DOL support is a boot/runtime primitive, not proof that a
particular recompiled game is fully playable: each game still needs its own
module, symbols, input map, graphics validation, and end-to-end boot tests.
A future game-specific address map, runtime code-patching range, HLE decision,
MMIO route, or revision-specific workaround must remain clearly identified and
reviewed rather than hidden in an unrelated platform edit.

See [RESEARCH.md](../docs/RESEARCH.md) and
[DEPENDENCIES.md](../docs/DEPENDENCIES.md) for architecture and provenance.
