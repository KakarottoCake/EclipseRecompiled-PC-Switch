"""Recover Kuribo runtime exports from a relocated KXE module prologue."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import struct

from .format import KxeFile, parse_kxe


@dataclass(frozen=True)
class RuntimeExport:
    name: str
    address: int
    call_offset: int


def _signed_16(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


def _read_c_string(code: bytes, code_base: int, address: int) -> str | None:
    offset = address - code_base
    if offset < 0 or offset >= len(code):
        return None
    end = code.find(b"\0", offset, min(len(code), offset + 512))
    if end < 0 or end == offset:
        return None
    raw = code[offset:end]
    if any(value < 0x20 or value >= 0x7F for value in raw):
        return None
    try:
        return raw.decode("ascii")
    except UnicodeDecodeError:
        return None


def recover_exports(kxe: KxeFile, code_base: int) -> tuple[RuntimeExport, ...]:
    """Recover calls to ``ctx->register_procedure(name, address)``.

    Kuribo's export table is built by the module prologue rather than stored in
    the KXE container. This small PowerPC constant-propagation pass recognizes
    the compiler-emitted callback sequence, including reused nonvolatile
    registers, and validates both arguments against the relocated code image.
    """

    code = kxe.apply_relocations(code_base, {})
    words = struct.unpack(f">{len(code) // 4}I", code[: len(code) & ~3])
    registers: list[int | None] = [None] * 32
    callback_registers: set[int] = set()
    counter_is_callback = False
    recovered: list[RuntimeExport] = []

    for index, word in enumerate(words):
        opcode = word >> 26
        rt_or_rs = (word >> 21) & 31
        ra = (word >> 16) & 31

        if opcode == 15 and ra == 0:  # lis/addis rD, 0, immediate
            registers[rt_or_rs] = (word & 0xFFFF) << 16
            callback_registers.discard(rt_or_rs)
        elif opcode == 14:  # addi
            source = registers[ra]
            registers[rt_or_rs] = (
                None
                if source is None
                else (source + _signed_16(word & 0xFFFF)) & 0xFFFFFFFF
            )
            callback_registers.discard(rt_or_rs)
        elif opcode == 24:  # ori
            source = registers[rt_or_rs]
            registers[ra] = (
                None if source is None else source | (word & 0xFFFF)
            )
            callback_registers.discard(ra)
        elif opcode == 32:  # lwz
            displacement = _signed_16(word & 0xFFFF)
            registers[rt_or_rs] = None
            callback_registers.discard(rt_or_rs)
            if ra == 29 and displacement == 0:
                callback_registers.add(rt_or_rs)
        elif opcode == 31 and ((word >> 1) & 0x3FF) == 444:  # or/mr
            rb = (word >> 11) & 31
            if rt_or_rs == rb:
                registers[ra] = registers[rt_or_rs]
                callback_registers.discard(ra)
                if rt_or_rs in callback_registers:
                    callback_registers.add(ra)
        elif (word & 0xFC1FFFFF) == 0x7C0903A6:  # mtctr rS
            counter_is_callback = rt_or_rs in callback_registers
        elif word == 0x4E800421:  # bctrl
            if counter_is_callback:
                name_address = registers[3]
                function_address = registers[4]
                if name_address is not None and function_address is not None:
                    name = _read_c_string(code, code_base, name_address)
                    if name and code_base <= function_address < code_base + len(code):
                        recovered.append(
                            RuntimeExport(name, function_address, index * 4)
                        )
            counter_is_callback = False
            for register in range(3, 13):
                registers[register] = None
                callback_registers.discard(register)
        elif opcode == 18 and word & 1:  # direct branch with link
            for register in range(3, 13):
                registers[register] = None
                callback_registers.discard(register)

    by_name: dict[str, RuntimeExport] = {}
    for item in recovered:
        previous = by_name.get(item.name)
        if previous is not None and previous.address != item.address:
            raise ValueError(
                f"conflicting runtime exports for {item.name!r}: "
                f"0x{previous.address:08x} and 0x{item.address:08x}"
            )
        by_name[item.name] = item
    return tuple(by_name.values())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", type=Path)
    parser.add_argument("--base", required=True, type=lambda value: int(value, 0))
    parser.add_argument("--require", type=Path, action="append", default=[])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    exports = recover_exports(parse_kxe(args.provider), args.base)
    addresses = {item.name: item.address for item in exports}
    missing: dict[str, list[str]] = {}
    for consumer_path in args.require:
        consumer = parse_kxe(consumer_path)
        unresolved = sorted({item.text for item in consumer.imports} - addresses.keys())
        if unresolved:
            missing[str(consumer_path)] = unresolved
    if missing:
        parser.error(f"unresolved required imports: {missing}")

    if args.json:
        print(json.dumps({name: f"0x{address:08x}" for name, address in sorted(addresses.items())}, indent=2))
    else:
        print(
            f"recovered {len(exports)} runtime exports from {args.provider}; "
            f"resolved all imports for {len(args.require)} consumer module(s)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
