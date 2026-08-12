"""Add a relocated KXE and an arena-reservation trampoline to a GameCube DOL."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import struct

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
    module_entry: int,
    resume_address: int | None,
) -> bytes:
    """Build boot/lifecycle code plus a 256-entry export capture table."""

    recorder_address = section_address + 0x60
    context_address = section_address + 0x100
    registry_address = section_address + 0x200

    boot = _boot_trampoline(original_entry, arena_hi)
    lifecycle_words = (
        _lis(4, context_address),
        _ori(4, 4, context_address),
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
    # guest table. BSE currently publishes fewer than the 256 reserved slots.
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

    section = bytearray(0xA20)
    section[0 : len(boot)] = boot
    struct.pack_into(f">{len(lifecycle_words)}I", section, 0x20, *lifecycle_words)
    struct.pack_into(f">{len(recorder_words)}I", section, 0x60, *recorder_words)
    struct.pack_into(
        ">5I",
        section,
        0x100,
        0,  # core version
        recorder_address,
        0,  # get_procedure is not needed by BSE
        arena_hi,
        0,
    )
    return bytes(section)


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
) -> bytes:
    """Return a DOL containing the original image plus relocated KXE code."""

    if len(base_dol) < DOL_HEADER_SIZE:
        raise ValueError("base file is smaller than a DOL header")
    slots = _free_text_slots(base_dol)
    if len(slots) < 2:
        raise ValueError("base DOL needs two free text slots")

    original_entry = _u32(base_dol, 0xE0)
    existing = _ranges(base_dol)
    kxe_range = DolRange("kxe code", code_base, len(kxe.code))
    trampoline = _lifecycle_section(
        trampoline_address,
        original_entry,
        code_base,
        code_base + kxe.entry_point_offset,
        lifecycle_resume_address,
    )
    trampoline_range = DolRange("boot trampoline", trampoline_address, len(trampoline))
    _check_candidate(kxe_range, existing)
    _check_candidate(trampoline_range, existing + [kxe_range])

    relocated = kxe.apply_relocations(code_base, imports or {})
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
        (slots[0], code_base, relocated),
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
    args = parser.parse_args()
    try:
        output = combine_dol(
            args.base_dol.read_bytes(),
            parse_kxe(args.kxe),
            args.base,
            args.trampoline,
            _load_imports(args.imports_json),
            args.lifecycle_hook,
            args.lifecycle_resume,
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
