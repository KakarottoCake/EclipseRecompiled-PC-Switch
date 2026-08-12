"""Inspect KXE containers without extracting or modifying them."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .format import parse_kxe


def describe(path: Path) -> dict[str, object]:
    kxe = parse_kxe(path)
    return {
        "path": str(path),
        "size": len(kxe.raw),
        "format_version": kxe.format_version,
        "kernel_version": kxe.kernel_version,
        "entry_point_offset": kxe.entry_point_offset,
        "code_size": len(kxe.code),
        "code_crc32_expected": f"0x{kxe.sections['code'].crc32:08x}",
        "code_crc32_actual": f"0x{kxe.code_crc32_actual:08x}",
        "code_crc32_matches": kxe.code_crc32_matches,
        "relocation_count": len(kxe.relocations),
        "relocation_types": kxe.relocation_counts(),
        "relocation_sources": kxe.relocation_source_counts(),
        "imports": [
            {"name": item.text, "crc32": f"0x{item.crc32:08x}"}
            for item in kxe.imports
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", type=Path, nargs="+")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    results = [describe(path) for path in args.paths]
    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True))
    else:
        for result in results:
            print(
                f"{result['path']}: code={result['code_size']} bytes, "
                f"relocations={result['relocation_count']}, "
                f"imports={len(result['imports'])}, "
                f"entry=+0x{result['entry_point_offset']:x}, "
                f"code-crc={'match' if result['code_crc32_matches'] else 'changed-after-checksum'}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
