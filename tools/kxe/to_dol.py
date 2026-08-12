"""Relocate a code-only KXE and wrap it as a one-section GameCube DOL."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct

from .format import KxeFile, RelocationError, parse_kxe


DOL_HEADER_SIZE = 0x100


def make_dol(kxe: KxeFile, code_base: int, imports: dict[str | int, int]) -> bytes:
    """Create a minimal DOL accepted by GameCube static-recompilation tools."""

    if code_base < 0 or code_base > 0xFFFFFFFF:
        raise ValueError("code base must be an unsigned 32-bit address")
    if code_base & 0x1F:
        raise ValueError("code base must be 32-byte aligned")
    if code_base + len(kxe.code) > 0x1_0000_0000:
        raise ValueError("code section wraps the 32-bit address space")

    code = kxe.apply_relocations(code_base, imports)
    header = bytearray(DOL_HEADER_SIZE)
    struct.pack_into(">I", header, 0x00, DOL_HEADER_SIZE)  # text file offset 0
    struct.pack_into(">I", header, 0x48, code_base)  # text load address 0
    struct.pack_into(">I", header, 0x90, len(code))  # text size 0
    struct.pack_into(">I", header, 0xE0, code_base + kxe.entry_point_offset)
    return bytes(header) + code


def _load_imports(path: Path | None) -> dict[str | int, int]:
    if path is None:
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("imports JSON must be an object mapping names or CRCs to addresses")
    result: dict[str | int, int] = {}
    for key, value in document.items():
        address = int(value, 0) if isinstance(value, str) else int(value)
        if key.lower().startswith("0x"):
            result[int(key, 0)] = address
        else:
            result[key] = address
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--base", required=True, type=lambda value: int(value, 0))
    parser.add_argument("--imports-json", type=Path)
    args = parser.parse_args()

    kxe = parse_kxe(args.input)
    try:
        output = make_dol(kxe, args.base, _load_imports(args.imports_json))
    except RelocationError as error:
        parser.error(str(error))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(output)
    print(
        f"wrote {args.output} ({len(output)} bytes), "
        f"guest entry 0x{args.base + kxe.entry_point_offset:08x}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
