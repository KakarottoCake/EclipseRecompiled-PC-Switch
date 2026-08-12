"""Parser and relocation model for Kuribo KXE version 0 files.

This is an independently written implementation based on public format facts,
PowerPC EABI relocation semantics, and black-box validation of user-owned KXE
files. It intentionally does not contain Kuribo loader source code.
"""

from __future__ import annotations

from dataclasses import dataclass
import binascii
import struct
from pathlib import Path
from typing import Callable, Mapping


MAGIC = b"KXER"
HEADER_SIZE = 132
SECTION_SIZE = 16
RELOCATION_SIZE = 16
BINARY_STRING_SIZE = 12
ABSENT = 0xFFFFFFFF

SECTION_NAMES = (
    "code",
    "data",
    "bss",
    "relocations",
    "imports",
    "exports",
)

R_PPC_NONE = 0
R_PPC_ADDR32 = 1
R_PPC_ADDR24 = 2
R_PPC_ADDR16 = 3
R_PPC_ADDR16_LO = 4
R_PPC_ADDR16_HI = 5
R_PPC_ADDR16_HA = 6
R_PPC_ADDR14 = 7
R_PPC_ADDR14_BRTAKEN = 8
R_PPC_ADDR14_BRNTAKEN = 9
R_PPC_REL24 = 10
R_PPC_REL14 = 11
R_PPC_REL32 = 26
R_PPC_EMB_SDA21 = 109

KNOWN_RELOCATION_TYPES = frozenset(
    range(R_PPC_NONE, R_PPC_REL14 + 1)
) | {R_PPC_REL32, R_PPC_EMB_SDA21}


class KxeError(ValueError):
    """The KXE container is malformed or unsupported."""


class RelocationError(KxeError):
    """A relocation cannot be resolved or safely applied."""


@dataclass(frozen=True)
class Section:
    name: str
    file_offset: int
    file_size: int
    crc32: int
    alignment: int
    flags: int
    reserved: int

    @property
    def absent(self) -> bool:
        return self.file_offset == ABSENT and self.file_size == ABSENT


@dataclass(frozen=True)
class BinaryString:
    offset: int
    length: int
    crc32: int
    text: str


@dataclass(frozen=True)
class Relocation:
    type: int
    affected_section: int
    source_section: int
    affected_offset: int
    source_offset: int
    source_addend: int


@dataclass(frozen=True)
class KxeFile:
    path: str | None
    raw: bytes
    format_version: int
    kernel_version: int
    declared_file_size: int
    flags: int
    sections: Mapping[str, Section]
    entry_point_offset: int
    relocations: tuple[Relocation, ...]
    imports: tuple[BinaryString, ...]

    @property
    def code(self) -> bytes:
        section = self.sections["code"]
        return self.raw[section.file_offset : section.file_offset + section.file_size]

    @property
    def entry_point_address_offset(self) -> int:
        return self.entry_point_offset

    @property
    def code_crc32_actual(self) -> int:
        return binascii.crc32(self.code) & 0xFFFFFFFF

    @property
    def code_crc32_matches(self) -> bool:
        expected = self.sections["code"].crc32
        return expected == 0 or expected == self.code_crc32_actual

    def relocation_counts(self) -> dict[int, int]:
        result: dict[int, int] = {}
        for relocation in self.relocations:
            result[relocation.type] = result.get(relocation.type, 0) + 1
        return dict(sorted(result.items()))

    def relocation_source_counts(self) -> dict[int, int]:
        result: dict[int, int] = {}
        for relocation in self.relocations:
            result[relocation.source_section] = (
                result.get(relocation.source_section, 0) + 1
            )
        return dict(sorted(result.items()))

    def apply_relocations(
        self,
        code_base: int,
        imports: Mapping[str | int, int] | Callable[[BinaryString], int | None],
    ) -> bytes:
        """Return a relocated copy of the code section.

        ``code_base`` is the chosen 32-bit guest address. Import mappings may
        use either exact names or CRC32 integers. Type 109 is deliberately
        rejected because it requires the original loader's trampoline scheme.
        """

        code_base = _u32(code_base)
        patched = bytearray(self.code)

        def resolve_import(record: BinaryString) -> int:
            if callable(imports):
                value = imports(record)
            else:
                value = imports.get(record.text)
                if value is None:
                    value = imports.get(record.crc32)
            if value is None:
                raise RelocationError(
                    f"unresolved import {record.text!r} (CRC32 0x{record.crc32:08x})"
                )
            return _u32(value)

        for index, relocation in enumerate(self.relocations):
            if relocation.affected_section != 0:
                raise RelocationError(
                    f"relocation {index}: affected section "
                    f"{relocation.affected_section} is unsupported"
                )
            source = _resolve_source(self, relocation, code_base, resolve_import, index)
            _apply_one(patched, relocation, code_base, source, index)

        return bytes(patched)


def _u32(value: int) -> int:
    return value & 0xFFFFFFFF


def _checked_slice(data: bytes, offset: int, size: int, label: str) -> bytes:
    if offset < 0 or size < 0 or offset > len(data) or size > len(data) - offset:
        raise KxeError(f"{label} extends beyond the KXE file")
    return data[offset : offset + size]


def _parse_section(data: bytes, name: str, offset: int) -> Section:
    file_offset, file_size, crc32, alignment, flags, reserved = struct.unpack_from(
        ">IIIBBH", data, offset
    )
    section = Section(name, file_offset, file_size, crc32, alignment, flags, reserved)
    if section.absent:
        return section
    if file_offset == ABSENT or file_size == ABSENT:
        raise KxeError(f"{name} has a partial absent-section sentinel")
    if alignment == 0 or alignment & (alignment - 1):
        raise KxeError(f"{name} alignment {alignment} is not a power of two")
    if flags & ~1:
        raise KxeError(f"{name} has unknown flags 0x{flags:02x}")
    if flags & 1:
        raise KxeError(f"{name} uses unsupported SZS compression")
    _checked_slice(data, file_offset, file_size, name)
    return section


def parse_kxe(
    source: bytes | bytearray | memoryview | str | Path,
    *,
    require_code_crc: bool = False,
) -> KxeFile:
    """Parse and validate a version-0 KXE container.

    The code checksum is diagnostic by default. The public Kuribo converter
    records it before applying static absolute-address relocations, so a valid
    emitted KXE may not match it. Set ``require_code_crc`` for inputs known not
    to have undergone that converter step.
    """
    path: str | None = None
    if isinstance(source, (str, Path)):
        path = str(source)
        data = Path(source).read_bytes()
    else:
        data = bytes(source)

    if len(data) < HEADER_SIZE:
        raise KxeError(f"file is smaller than the {HEADER_SIZE}-byte KXE v0 header")
    if data[:4] != MAGIC:
        raise KxeError("invalid KXE magic")

    format_version, kernel_version, declared_size, flags = struct.unpack_from(
        ">HHII", data, 4
    )
    if format_version != 0:
        raise KxeError(f"unsupported KXE format version {format_version}")
    if kernel_version != 0:
        raise KxeError(f"unsupported Kuribo kernel ABI version {kernel_version}")
    if declared_size not in (0, len(data)):
        raise KxeError(
            f"declared file size {declared_size} does not match actual size {len(data)}"
        )

    sections: dict[str, Section] = {}
    for index, name in enumerate(SECTION_NAMES):
        sections[name] = _parse_section(data, name, 16 + index * SECTION_SIZE)

    code = sections["code"]
    relocation_section = sections["relocations"]
    if code.absent or code.file_size == 0:
        raise KxeError("code section is required")
    if relocation_section.absent:
        raise KxeError("relocation section is required")
    if relocation_section.file_size % RELOCATION_SIZE:
        raise KxeError("relocation section size is not a multiple of 16")

    entry_point_offset = struct.unpack_from(">I", data, 112)[0]
    if entry_point_offset >= code.file_size:
        raise KxeError("entry point lies outside the code section")

    if code.crc32 and require_code_crc:
        actual_crc = binascii.crc32(
            _checked_slice(data, code.file_offset, code.file_size, "code")
        ) & 0xFFFFFFFF
        if actual_crc != code.crc32:
            raise KxeError(
                f"code CRC32 mismatch: expected 0x{code.crc32:08x}, "
                f"got 0x{actual_crc:08x}"
            )

    relocations: list[Relocation] = []
    external_indexes: list[int] = []
    for item_offset in range(
        relocation_section.file_offset,
        relocation_section.file_offset + relocation_section.file_size,
        RELOCATION_SIZE,
    ):
        reloc_type, affected_section, source_section, pad, affected, source_offset, addend = (
            struct.unpack_from(">BBBBIII", data, item_offset)
        )
        if pad != 0:
            raise KxeError("relocation reserved byte is nonzero")
        if reloc_type not in KNOWN_RELOCATION_TYPES:
            raise KxeError(f"unknown PowerPC relocation type {reloc_type}")
        if affected_section not in (0, 1, 2):
            raise KxeError(f"invalid affected section {affected_section}")
        if source_section not in (0, 1, 2, 0xFD, 0xFF):
            raise KxeError(f"invalid source section {source_section}")
        relocation = Relocation(
            reloc_type,
            affected_section,
            source_section,
            affected,
            source_offset,
            addend,
        )
        relocations.append(relocation)
        if source_section == 0xFF:
            external_indexes.append(source_offset)

    imports: list[BinaryString] = []
    import_count = max(external_indexes, default=-1) + 1
    import_section = sections["imports"]
    if import_count:
        if import_section.absent:
            raise KxeError("external relocations exist without an imports section")
        table_size = import_count * BINARY_STRING_SIZE
        if table_size > import_section.file_size:
            raise KxeError("import table is smaller than referenced import indexes")
        for index in range(import_count):
            item_offset = import_section.file_offset + index * BINARY_STRING_SIZE
            string_offset, string_length, crc32 = struct.unpack_from(">III", data, item_offset)
            raw_string = _checked_slice(data, string_offset, string_length, f"import {index}")
            try:
                text = raw_string.decode("utf-8")
            except UnicodeDecodeError as error:
                raise KxeError(f"import {index} is not valid UTF-8") from error
            imports.append(BinaryString(string_offset, string_length, crc32, text))
    elif not import_section.absent and import_section.file_size != 0:
        raise KxeError("imports section has data but no relocation references it")

    for index in external_indexes:
        if index >= len(imports):
            raise KxeError(f"relocation references missing import index {index}")

    return KxeFile(
        path,
        data,
        format_version,
        kernel_version,
        declared_size,
        flags,
        sections,
        entry_point_offset,
        tuple(relocations),
        tuple(imports),
    )


def _resolve_source(
    kxe: KxeFile,
    relocation: Relocation,
    code_base: int,
    resolve_import: Callable[[BinaryString], int],
    index: int,
) -> int:
    if relocation.source_section == 0:
        base = code_base
    elif relocation.source_section == 0xFD:
        base = 0
    elif relocation.source_section == 0xFF:
        if relocation.source_offset >= len(kxe.imports):
            raise RelocationError(
                f"relocation {index}: import index {relocation.source_offset} is out of range"
            )
        base = resolve_import(kxe.imports[relocation.source_offset])
        return _u32(base + relocation.source_addend)
    else:
        raise RelocationError(
            f"relocation {index}: source section {relocation.source_section} "
            "is not supported by the version-0 code-only pipeline"
        )
    return _u32(base + relocation.source_offset + relocation.source_addend)


def _apply_one(
    code: bytearray,
    relocation: Relocation,
    code_base: int,
    source: int,
    index: int,
) -> None:
    offset = relocation.affected_offset
    kind = relocation.type
    if kind in (R_PPC_ADDR16, R_PPC_ADDR16_LO, R_PPC_ADDR16_HI, R_PPC_ADDR16_HA):
        width = 2
    else:
        width = 4
    if offset > len(code) - width:
        raise RelocationError(f"relocation {index}: affected offset is outside code")

    affected_address = _u32(code_base + offset)
    if kind == R_PPC_NONE:
        return
    if kind == R_PPC_EMB_SDA21:
        raise RelocationError(
            f"relocation {index}: type 109 needs a runtime trampoline and is unsupported"
        )
    if width == 2:
        if kind in (R_PPC_ADDR16, R_PPC_ADDR16_LO):
            value = source
        elif kind == R_PPC_ADDR16_HI:
            value = source >> 16
        else:
            value = (source + 0x8000) >> 16
        struct.pack_into(">H", code, offset, value & 0xFFFF)
        return

    original = struct.unpack_from(">I", code, offset)[0]
    if kind == R_PPC_ADDR32:
        value = source
    elif kind == R_PPC_ADDR24:
        value = (original & ~0x03FFFFFC) | (source & 0x03FFFFFC)
    elif kind in (R_PPC_ADDR14, R_PPC_ADDR14_BRTAKEN, R_PPC_ADDR14_BRNTAKEN):
        value = (original & ~0x0000FFFC) | (source & 0x0000FFFC)
    elif kind == R_PPC_REL24:
        delta_signed = source - affected_address
        if delta_signed < -0x02000000 or delta_signed > 0x01FFFFFC:
            raise RelocationError(
                f"relocation {index}: REL24 target is outside the signed 26-bit range"
            )
        if delta_signed & 3:
            raise RelocationError(f"relocation {index}: REL24 target is not aligned")
        delta = _u32(delta_signed)
        value = (original & ~0x03FFFFFC) | (delta & 0x03FFFFFC)
    elif kind == R_PPC_REL14:
        delta_signed = source - affected_address
        if delta_signed < -0x00008000 or delta_signed > 0x00007FFC:
            raise RelocationError(
                f"relocation {index}: REL14 target is outside the signed 16-bit range"
            )
        if delta_signed & 3:
            raise RelocationError(f"relocation {index}: REL14 target is not aligned")
        delta = _u32(delta_signed)
        value = (original & ~0x0000FFFC) | (delta & 0x0000FFFC)
    elif kind == R_PPC_REL32:
        value = _u32(source - affected_address)
    else:
        raise RelocationError(f"relocation {index}: unsupported type {kind}")
    struct.pack_into(">I", code, offset, _u32(value))
