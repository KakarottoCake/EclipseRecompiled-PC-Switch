from __future__ import annotations

import binascii
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.kxe.format import (  # noqa: E402
    KxeError,
    R_PPC_ADDR16_HA,
    R_PPC_ADDR16_HI,
    R_PPC_ADDR16_LO,
    R_PPC_ADDR32,
    R_PPC_REL24,
    RelocationError,
    parse_kxe,
)
from tools.kxe.to_dol import make_dol  # noqa: E402
from tools.kxe.combine_dol import combine_dol  # noqa: E402


def make_kxe(relocations: list[tuple[int, int, int, int, int, int]], imports=()):
    code = bytearray(32)
    struct.pack_into(">I", code, 16, 0x48000001)
    header_size = 132
    code_offset = 160
    reloc_offset = code_offset + len(code)
    relocation_data = b"".join(struct.pack(">BBBBIII", *item) for item in relocations)
    import_offset = reloc_offset + len(relocation_data)
    table_size = len(imports) * 12
    strings_offset = import_offset + table_size
    strings = b""
    records = b""
    for text, crc in imports:
        encoded = text.encode()
        records += struct.pack(">III", strings_offset + len(strings), len(encoded), crc)
        strings += encoded
    file_size = strings_offset + len(strings)
    raw = bytearray(file_size)
    raw[:4] = b"KXER"
    struct.pack_into(">HHII", raw, 4, 0, 0, file_size, 0)

    def section(index, offset, size, crc=0):
        struct.pack_into(">IIIBBH", raw, 16 + index * 16, offset, size, crc, 32, 0, 0)

    section(0, code_offset, len(code), binascii.crc32(code) & 0xFFFFFFFF)
    for index in (1, 2, 5):
        section(index, 0xFFFFFFFF, 0xFFFFFFFF)
    section(3, reloc_offset, len(relocation_data))
    section(4, import_offset, len(records) + len(strings))
    struct.pack_into(">I", raw, 112, 0)
    struct.pack_into(">IIIBBH", raw, 116, 0xFFFFFFFF, 0xFFFFFFFF, 0, 32, 0, 0)
    raw[code_offset : code_offset + len(code)] = code
    raw[reloc_offset : reloc_offset + len(relocation_data)] = relocation_data
    raw[import_offset : import_offset + len(records)] = records
    raw[strings_offset : strings_offset + len(strings)] = strings
    return bytes(raw)


class KxeParserTests(unittest.TestCase):
    def test_parses_imports_and_relocations(self):
        raw = make_kxe(
            [(R_PPC_ADDR32, 0, 0xFF, 0, 0, 0, 4)],
            [("example", 0x12345678)],
        )
        kxe = parse_kxe(raw)
        self.assertEqual(kxe.imports[0].text, "example")
        self.assertEqual(kxe.relocations[0].source_addend, 4)

    def test_applies_internal_and_external_relocations_big_endian(self):
        raw = make_kxe(
            [
                (R_PPC_ADDR32, 0, 0, 0, 0, 8, 4),
                (R_PPC_ADDR16_LO, 0, 0xFF, 0, 4, 0, 0),
                (R_PPC_ADDR16_HI, 0, 0xFF, 0, 6, 0, 0),
                (R_PPC_ADDR16_HA, 0, 0xFF, 0, 8, 0, 0),
                (R_PPC_REL24, 0, 0, 0, 16, 24, 0),
            ],
            [("external", 0x9ABCDEF0)],
        )
        patched = parse_kxe(raw).apply_relocations(0x81000000, {"external": 0x8123F678})
        self.assertEqual(struct.unpack_from(">I", patched, 0)[0], 0x8100000C)
        self.assertEqual(struct.unpack_from(">H", patched, 4)[0], 0xF678)
        self.assertEqual(struct.unpack_from(">H", patched, 6)[0], 0x8123)
        self.assertEqual(struct.unpack_from(">H", patched, 8)[0], 0x8124)
        self.assertEqual(struct.unpack_from(">I", patched, 16)[0], 0x48000009)

    def test_rejects_crc_mismatch(self):
        raw = bytearray(make_kxe([]))
        raw[160] ^= 1
        with self.assertRaisesRegex(KxeError, "CRC32 mismatch"):
            parse_kxe(raw, require_code_crc=True)

    def test_reports_but_accepts_converter_style_crc_mismatch(self):
        raw = bytearray(make_kxe([]))
        raw[160] ^= 1
        kxe = parse_kxe(raw)
        self.assertFalse(kxe.code_crc32_matches)

    def test_rejects_out_of_range_affected_offset(self):
        raw = make_kxe([(R_PPC_ADDR32, 0, 0, 0, 31, 0, 0)])
        with self.assertRaisesRegex(RelocationError, "outside code"):
            parse_kxe(raw).apply_relocations(0x81000000, {})

    def test_rejects_out_of_range_relative_branch(self):
        raw = make_kxe([(R_PPC_REL24, 0, 0xFD, 0, 16, 0x84000000, 0)])
        with self.assertRaisesRegex(RelocationError, "signed 26-bit range"):
            parse_kxe(raw).apply_relocations(0x80000000, {})

    def test_rejects_unaligned_relative_branch(self):
        raw = make_kxe([(R_PPC_REL24, 0, 0xFD, 0, 16, 0x80000001, 0)])
        with self.assertRaisesRegex(RelocationError, "not aligned"):
            parse_kxe(raw).apply_relocations(0x80000000, {})

    def test_rejects_unknown_source_section(self):
        raw = bytearray(make_kxe([]))
        reloc_offset = 192
        raw.extend(struct.pack(">BBBBIII", R_PPC_ADDR32, 0, 7, 0, 0, 0, 0))
        struct.pack_into(">II", raw, 16 + 3 * 16, reloc_offset, 16)
        struct.pack_into(">I", raw, 8, len(raw))
        with self.assertRaisesRegex(KxeError, "invalid source section"):
            parse_kxe(raw)

    def test_wraps_relocated_kxe_as_gamecube_dol(self):
        kxe = parse_kxe(make_kxe([(R_PPC_ADDR32, 0, 0, 0, 0, 8, 0)]))
        dol = make_dol(kxe, 0x81700000, {})
        self.assertEqual(struct.unpack_from(">I", dol, 0x00)[0], 0x100)
        self.assertEqual(struct.unpack_from(">I", dol, 0x48)[0], 0x81700000)
        self.assertEqual(struct.unpack_from(">I", dol, 0x90)[0], 32)
        self.assertEqual(struct.unpack_from(">I", dol, 0xE0)[0], 0x81700000)
        self.assertEqual(struct.unpack_from(">I", dol, 0x100)[0], 0x81700008)

    def test_combines_kxe_and_arena_trampoline_with_existing_dol(self):
        base = bytearray(0x120)
        struct.pack_into(">I", base, 0x00, 0x100)
        struct.pack_into(">I", base, 0x48, 0x80003100)
        struct.pack_into(">I", base, 0x90, 0x20)
        struct.pack_into(">I", base, 0xE0, 0x80003100)
        kxe = parse_kxe(make_kxe([]))
        combined = combine_dol(base, kxe, 0x81700000, 0x81780000)
        self.assertEqual(struct.unpack_from(">I", combined, 0xE0)[0], 0x81780000)
        self.assertEqual(struct.unpack_from(">I", combined, 0x4C)[0], 0x81700000)
        self.assertEqual(struct.unpack_from(">I", combined, 0x50)[0], 0x81780000)
        trampoline_offset = struct.unpack_from(">I", combined, 0x08)[0]
        words = struct.unpack_from(">8I", combined, trampoline_offset)
        self.assertEqual(words[:3], (0x3D808170, 0x3D608000, 0x918B0034))
        self.assertEqual(words[3:7], (0x3D808000, 0x618C3100, 0x7D8903A6, 0x4E800420))

    def test_combined_dol_rejects_guest_overlap(self):
        base = bytearray(0x120)
        struct.pack_into(">I", base, 0x00, 0x100)
        struct.pack_into(">I", base, 0x48, 0x81700000)
        struct.pack_into(">I", base, 0x90, 0x20)
        struct.pack_into(">I", base, 0xE0, 0x81700000)
        with self.assertRaisesRegex(ValueError, "overlaps text"):
            combine_dol(base, parse_kxe(make_kxe([])), 0x81700000, 0x80420000)

    def test_combined_dol_replaces_dynamic_loader_with_bse_lifecycle(self):
        base = bytearray(0x140)
        struct.pack_into(">I", base, 0x00, 0x100)
        struct.pack_into(">I", base, 0x48, 0x802A7440)
        struct.pack_into(">I", base, 0x90, 0x40)
        struct.pack_into(">I", base, 0xE0, 0x802A7440)
        combined = combine_dol(
            base,
            parse_kxe(make_kxe([])),
            0x81700000,
            0x81780000,
            lifecycle_hook_address=0x802A744C,
            lifecycle_resume_address=0x802A7450,
        )
        hook = struct.unpack_from(">I", combined, 0x10C)[0]
        self.assertEqual(hook, 0x494D8BD4)
        shim_offset = struct.unpack_from(">I", combined, 0x08)[0]
        context = struct.unpack_from(">5I", combined, shim_offset + 0x100)
        self.assertEqual(context, (0, 0x81780060, 0, 0x81700000, 0))


if __name__ == "__main__":
    unittest.main()
