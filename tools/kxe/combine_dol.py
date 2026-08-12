"""Add a relocated KXE and an arena-reservation trampoline to a GameCube DOL."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import struct
from typing import Mapping, Sequence

from .exports import recover_exports
from .format import KxeFile, RelocationError, parse_kxe


DOL_HEADER_SIZE = 0x100
MEM1_START = 0x80000000
MEM1_END = 0x81800000
TEXT_COUNT = 7
DATA_COUNT = 11


@dataclass(frozen=True)
class DolRange:
    name: str
    address: int
    size: int

    @property
    def end(self) -> int:
        return self.address + self.size


@dataclass(frozen=True)
class ModulePlacement:
    kxe: KxeFile
    address: int
    imports: Mapping[str | int, int]

    @property
    def entry(self) -> int:
        return self.address + self.kxe.entry_point_offset


def _u32(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def _align(value: int, alignment: int) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


def _ranges(dol: bytes) -> list[DolRange]:
    result: list[DolRange] = []
    for index in range(TEXT_COUNT):
        size = _u32(dol, 0x90 + index * 4)
        if size:
            result.append(DolRange(f"text[{index}]", _u32(dol, 0x48 + index * 4), size))
    for index in range(DATA_COUNT):
        size = _u32(dol, 0xAC + index * 4)
        if size:
            result.append(DolRange(f"data[{index}]", _u32(dol, 0x64 + index * 4), size))
    bss_size = _u32(dol, 0xDC)
    if bss_size:
        result.append(DolRange("bss", _u32(dol, 0xD8), bss_size))
    return result


def _check_candidate(candidate: DolRange, existing: list[DolRange]) -> None:
    if candidate.address < MEM1_START or candidate.end > MEM1_END:
        raise ValueError(f"{candidate.name} does not fit in GameCube MEM1")
    if candidate.address & 3:
        raise ValueError(f"{candidate.name} address is not word aligned")
    for item in existing:
        if candidate.address < item.end and item.address < candidate.end:
            raise ValueError(
                f"{candidate.name} overlaps {item.name}: "
                f"0x{item.address:08x}-0x{item.end:08x}"
            )


def _free_text_slots(dol: bytes) -> list[int]:
    return [index for index in range(TEXT_COUNT) if _u32(dol, 0x90 + index * 4) == 0]


def _boot_trampoline(original_entry: int, arena_hi: int) -> bytes:
    """Return PPC code that reserves high MEM1, then jumps to the old entry."""

    if arena_hi & 0xFFFF:
        raise ValueError("arena high must be 64 KiB aligned for the boot trampoline")
    words = (
        0x3D800000 | ((arena_hi >> 16) & 0xFFFF),  # lis r12, arena_hi@h
        0x3D608000,  # lis r11, 0x8000
        0x918B0034,  # stw r12, 0x34(r11)
        0x3D800000 | ((original_entry >> 16) & 0xFFFF),  # lis r12, entry@h
        0x618C0000 | (original_entry & 0xFFFF),  # ori r12, r12, entry@l
        0x7D8903A6,  # mtctr r12
        0x4E800420,  # bctr
        0x60000000,  # nop/padding
    )
    return struct.pack(">8I", *words)


def _lis(register: int, value: int) -> int:
    return (15 << 26) | (register << 21) | ((value >> 16) & 0xFFFF)


def _ori(destination: int, source: int, value: int) -> int:
    return (24 << 26) | (source << 21) | (destination << 16) | (value & 0xFFFF)


def _lifecycle_section(
    section_address: int,
    original_entry: int,
    arena_hi: int,
    modules: Sequence[tuple[int, int]],
    resume_address: int | None,
) -> bytes:
    """Build boot/lifecycle code plus a 512-entry export capture table."""

    getter_address = section_address + 0xA0
    recorder_address = section_address + 0x100
    context_address = section_address + 0x140
    registry_address = section_address + 0x200

    boot = _boot_trampoline(original_entry, arena_hi)
    lifecycle_words: tuple[int, ...] = ()
    for index, (_, module_entry) in enumerate(modules):
        module_context = context_address + index * 20
        lifecycle_words += (
            _lis(4, module_context),
            _ori(4, 4, module_context),
            0x38600000,  # li r3, KURIBO_REASON_LOAD
            _lis(12, module_entry),
            _ori(12, 12, module_entry),
            0x7D8903A6,  # mtctr r12
            0x4E800421,  # bctrl
        )
    if resume_address is None:
        lifecycle_words += (0x4E800020,)  # blr
    else:
        lifecycle_words += (
            _lis(12, resume_address),
            _ori(12, 12, resume_address),
            0x7D8903A6,  # mtctr r12
            0x4E800420,  # bctr
        )

    # register_procedure(name=r3, address=r4): append the pair to a fixed
    # guest table. The combined image reserves 512 name/address pairs.
    recorder_words = (
        _lis(5, registry_address),
        _ori(5, 5, registry_address),
        0x80C50000,  # lwz r6, 0(r5)
        0x54C71838,  # slwi r7, r6, 3
        0x38E70004,  # addi r7, r7, 4
        0x7CE53A14,  # add r7, r5, r7
        0x90670000,  # stw r3, 0(r7)
        0x90870004,  # stw r4, 4(r7)
        0x38C60001,  # addi r6, r6, 1
        0x90C50000,  # stw r6, 0(r5)
        0x4E800020,  # blr
    )

    # get_procedure(name=r3): byte-compare the requested name against every
    # registered string, returning its address or zero. The registered name
    # pointers remain valid because all KXEs are permanently resident.
    getter_words = (
        _lis(5, registry_address),
        _ori(5, 5, registry_address),
        0x80C50000,  # lwz r6, 0(r5)
        0x2C060000,  # cmpwi r6, 0
        0x41820040,  # beq empty
        0x39050004,  # addi r8, r5, 4
        0x7CC903A6,  # mtctr r6
        0x7C691B78,  # outer: mr r9, r3
        0x81480000,  # lwz r10, 0(r8)
        0x89690000,  # inner: lbz r11, 0(r9)
        0x898A0000,  # lbz r12, 0(r10)
        0x7C0B6000,  # cmpw r11, r12
        0x40820018,  # bne next
        0x2C0B0000,  # cmpwi r11, 0
        0x41820020,  # beq found
        0x39290001,  # addi r9, r9, 1
        0x394A0001,  # addi r10, r10, 1
        0x4BFFFFE0,  # b inner
        0x39080008,  # next: addi r8, r8, 8
        0x4200FFD0,  # bdnz outer
        0x38600000,  # empty: li r3, 0
        0x4E800020,  # blr
        0x80680004,  # found: lwz r3, 4(r8)
        0x4E800020,  # blr
    )

    if len(lifecycle_words) * 4 > getter_address - (section_address + 0x20):
        raise ValueError("too many module lifecycle calls for the guest shim")
    if len(modules) * 20 > registry_address - context_address:
        raise ValueError("too many module contexts for the guest shim")

    section = bytearray(0x1220)
    section[0 : len(boot)] = boot
    struct.pack_into(f">{len(lifecycle_words)}I", section, 0x20, *lifecycle_words)
    struct.pack_into(f">{len(getter_words)}I", section, 0xA0, *getter_words)
    struct.pack_into(f">{len(recorder_words)}I", section, 0x100, *recorder_words)
    for index, (module_start, _) in enumerate(modules):
        struct.pack_into(
            ">5I",
            section,
            0x140 + index * 20,
            0,  # core version
            recorder_address,
            getter_address,
            module_start,
            0,
        )
    return bytes(section)


def _module_blob(placements: Sequence[ModulePlacement]) -> tuple[int, bytes]:
    if not placements:
        raise ValueError("at least one KXE module is required")
    ordered = sorted(placements, key=lambda item: item.address)
    blob_start = ordered[0].address
    blob_end = max(item.address + len(item.kxe.code) for item in ordered)
    blob = bytearray(blob_end - blob_start)
    previous_end = blob_start
    for index, item in enumerate(ordered):
        if item.address & 3:
            raise ValueError(f"module {index} address is not word aligned")
        if item.address < previous_end:
            raise ValueError(f"module {index} overlaps the preceding KXE module")
        relocated = item.kxe.apply_relocations(item.address, item.imports)
        offset = item.address - blob_start
        blob[offset : offset + len(relocated)] = relocated
        previous_end = item.address + len(relocated)
    return blob_start, bytes(blob)


def _guest_file_offset(dol: bytes, address: int) -> int:
    for index in range(TEXT_COUNT):
        size = _u32(dol, 0x90 + index * 4)
        start = _u32(dol, 0x48 + index * 4)
        if size and start <= address <= start + size - 4:
            return _u32(dol, 0x00 + index * 4) + address - start
    raise ValueError(f"guest patch address 0x{address:08x} is outside DOL text")


def _relative_branch(source: int, target: int) -> int:
    delta = target - source
    if delta < -0x02000000 or delta > 0x01FFFFFC:
        raise ValueError("lifecycle hook is outside the REL24 branch range")
    if delta & 3:
        raise ValueError("lifecycle hook is not word aligned")
    return 0x48000000 | (delta & 0x03FFFFFC)


def combine_dol(
    base_dol: bytes,
    kxe: KxeFile,
    code_base: int,
    trampoline_address: int,
    imports: dict[str | int, int] | None = None,
    lifecycle_hook_address: int | None = None,
    lifecycle_resume_address: int | None = None,
    additional_modules: Sequence[ModulePlacement] = (),
) -> bytes:
    """Return a DOL containing the original image plus relocated KXE code."""

    if len(base_dol) < DOL_HEADER_SIZE:
        raise ValueError("base file is smaller than a DOL header")
    slots = _free_text_slots(base_dol)
    if len(slots) < 2:
        raise ValueError("base DOL needs two free text slots")

    original_entry = _u32(base_dol, 0xE0)
    existing = _ranges(base_dol)
    placements = (ModulePlacement(kxe, code_base, imports or {}), *additional_modules)
    module_base, module_blob = _module_blob(placements)
    kxe_range = DolRange("kxe modules", module_base, len(module_blob))
    trampoline = _lifecycle_section(
        trampoline_address,
        original_entry,
        module_base,
        tuple((item.address, item.entry) for item in placements),
        lifecycle_resume_address,
    )
    trampoline_range = DolRange("boot trampoline", trampoline_address, len(trampoline))
    _check_candidate(kxe_range, existing)
    _check_candidate(trampoline_range, existing + [kxe_range])

    output = bytearray(base_dol)
    if lifecycle_hook_address is not None:
        if lifecycle_resume_address is None:
            raise ValueError("a lifecycle hook requires a resume address")
        hook_offset = _guest_file_offset(output, lifecycle_hook_address)
        struct.pack_into(
            ">I",
            output,
            hook_offset,
            _relative_branch(lifecycle_hook_address, trampoline_address + 0x20),
        )
    for slot, address, payload in (
        (slots[0], module_base, module_blob),
        (slots[1], trampoline_address, trampoline),
    ):
        file_offset = _align(len(output), 32)
        output.extend(b"\0" * (file_offset - len(output)))
        output.extend(payload)
        struct.pack_into(">I", output, 0x00 + slot * 4, file_offset)
        struct.pack_into(">I", output, 0x48 + slot * 4, address)
        struct.pack_into(">I", output, 0x90 + slot * 4, len(payload))
    struct.pack_into(">I", output, 0xE0, trampoline_address)
    return bytes(output)


def _load_imports(path: Path | None) -> dict[str | int, int]:
    if path is None:
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("imports JSON must be an object")
    result: dict[str | int, int] = {}
    for key, value in document.items():
        address = int(value, 0) if isinstance(value, str) else int(value)
        result[int(key, 0) if key.lower().startswith("0x") else key] = address
    return result


def _module_argument(value: str) -> tuple[Path, int]:
    try:
        path, address = value.rsplit("@", 1)
        return Path(path), int(address, 0)
    except (ValueError, TypeError) as error:
        raise argparse.ArgumentTypeError("module must be PATH@ADDRESS") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_dol", type=Path)
    parser.add_argument("kxe", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--base", required=True, type=lambda value: int(value, 0))
    parser.add_argument("--trampoline", required=True, type=lambda value: int(value, 0))
    parser.add_argument("--lifecycle-hook", type=lambda value: int(value, 0))
    parser.add_argument("--lifecycle-resume", type=lambda value: int(value, 0))
    parser.add_argument("--imports-json", type=Path)
    parser.add_argument(
        "--module",
        type=_module_argument,
        action="append",
        default=[],
        metavar="PATH@ADDRESS",
        help="additional KXE whose imports are resolved from the first module",
    )
    args = parser.parse_args()
    try:
        provider = parse_kxe(args.kxe)
        provider_exports = {
            item.name: item.address for item in recover_exports(provider, args.base)
        }
        additions = tuple(
            ModulePlacement(parse_kxe(path), address, provider_exports)
            for path, address in args.module
        )
        output = combine_dol(
            args.base_dol.read_bytes(),
            provider,
            args.base,
            args.trampoline,
            _load_imports(args.imports_json),
            args.lifecycle_hook,
            args.lifecycle_resume,
            additions,
        )
    except (ValueError, RelocationError) as error:
        parser.error(str(error))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(output)
    print(
        f"wrote {args.output} ({len(output)} bytes), "
        f"entry 0x{args.trampoline:08x}, reserved arena high 0x{args.base:08x}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
