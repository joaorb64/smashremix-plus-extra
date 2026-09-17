import os
from typing import Callable, Optional

from SSB import N64, NALEnum, ReplaceData, SSBtbl, FinishROM, process
from .vpk0 import compress as vpk0_compress
from .file_appender import pad_rom_file


# ---------------------------------------------------------------------------
# Modified files - previously the MODIFY rows in incremental_extra.csv.
# (file_id, local_path, tbl_offset, res_offset, compression_level)
# ---------------------------------------------------------------------------
MODIFIED_FILES = [
    (0x000B, "scripts/000B.bin",  0x01EF8, 0x3FFFC, 2),  # 1P icons
    (0x000C, "scripts/000C.bin",  0x00130, 0x3FFFC, 2),  # 1P nameplates
    (0x0011, "scripts/0011.bin",  0x00038, 0x3FFFC, 2),  # CSS nameplates
    # 2D series logos (CSS, SSS)
    (0x0014, "scripts/0014.bin",  0x00610, 0x3FFFC, 2),
    # 3D series logos (Results, Data)
    (0x0023, "scripts/0023.bin",  0x00004, 0x3FFFC, 2),
    (0x153E, "scripts/153E.bin",  0x00970, 0x3FFFC, 2),  # Stage icons 2
    (0x0A05, "scripts/0A05.bin",  0x00810, 0x3FFFC, 2),  # Character portraits
    (0x0A06, "scripts/0A06.bin",  0x00210, 0x3FFFC, 2),  # CSS images
    (0x10F5, "scripts/10F5.bin",  0x02410, 0x3FFFC, 2),  # Data screen bios
    # Data screen names, works, special attacks
    (0x10F6, "scripts/10F6.bin",  0x00A10, 0x3FFFC, 2),
]


def _parse_reqlist(path: Optional[str]) -> bytes:
    """Read a compiled reqlist text file → packed 2-byte big-endian file IDs."""
    if not path or not os.path.exists(path):
        return b""
    idx = bytearray()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.upper().startswith("END OF"):
                continue
            hex_id = line.split()[0]
            idx.extend(int(hex_id, 16).to_bytes(2, "big"))
    return bytes(idx)


class ROMInjector:
    """
    Loads an SSB64 ROM, accepts modify/add operations, then writes the result.

    Files with compression_level > 0 are VPK0-compressed before being stored.
    The isCompressed bit in the file-table entry is set automatically by
    SSBtbl.tblentry.tobytes() when it detects a "vpk0" header.

    Usage:
        injector = ROMInjector("original.z64", "original_extra.z64")
        injector.modify(0x000C, "scripts/000C.bin", 0x00130, 0x3FFFC, compression_level=2)
        injector.add("build/char/main.bin", 0x0190, 0x06D8,
                     reqlist_path="build/char/main_reqlist.txt", compression_level=2)
        injector.save(on_progress=print)
    """

    def __init__(self, input_rom: str, output_rom: str):
        with open(input_rom, "rb") as f:
            self.n64 = N64(f.read())
        self.entries = SSBtbl.fromROM(self.n64)
        self._initial_count = len(self.entries)
        self._output_rom = output_rom
        self._modified = 0
        self._added = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def modify(
        self,
        file_id: int,
        file_path: str,
        tbl_offset: int,
        res_offset: int,
        reqlist_path: Optional[str] = None,
        compression_level: int = 0,
    ) -> None:
        """Replace an existing ROM file, optionally VPK0-compressing it first."""
        with open(file_path, "rb") as f:
            raw = f.read()
        data = vpk0_compress(raw) if compression_level > 0 else raw
        idx = _parse_reqlist(reqlist_path)

        entry = self.entries[file_id]
        entry.data = data
        entry.idx = idx
        entry.tbl = tbl_offset if tbl_offset != 0x3FFFC else None
        entry.res = res_offset if res_offset != 0x3FFFC else None
        self._modified += 1

    def add(
        self,
        file_path: str,
        tbl_offset: int,
        res_offset: int,
        reqlist_path: Optional[str] = None,
        compression_level: int = 0,
    ) -> None:
        """Append a new file to the ROM, optionally VPK0-compressing it first."""
        with open(file_path, "rb") as f:
            raw = f.read()
        data = vpk0_compress(raw) if compression_level > 0 else raw
        idx = _parse_reqlist(reqlist_path)

        tbl = tbl_offset if tbl_offset != 0x3FFFC else None
        res = res_offset if res_offset != 0x3FFFC else None
        self.entries.append(data, idx, tbl, res)
        self._added += 1

    def _calculate_min_rom_size(self) -> int:
        """
        Calculate the minimum ROM size needed for all entries and file table.

        From SSBFileInjector.cpp lines 976-994:
        Computes: file_table_end + all_file_data + req_indices, rounded to 0x10
        """
        # Rough estimate: sum of all file data sizes
        total_data = sum(len(entry.data) if hasattr(entry, 'data') and entry.data else 0
                         for entry in self.entries)

        # File table: ~12 bytes per entry + terminator
        file_table_size = len(self.entries) * 0xC + 0xC

        # Conservative estimate: file_table + all data
        min_size = file_table_size + total_data

        # Round up to nearest 16-byte boundary (like SSBFileInjector)
        if min_size % 0x10 != 0:
            min_size += 0x10 - (min_size % 0x10)

        return min_size

    def save(self, on_progress: Optional[Callable[[str], None]] = None) -> None:
        """Rebuild the file table, recalculate the CRC, and write the ROM."""
        if on_progress:
            on_progress(
                f"Injecting {self._modified} modified + {self._added} new files …"
            )

        new_tbl = self.entries.tobytes()

        # Calculate minimum ROM size needed for all entries + file table
        # This mimics SSBFileInjector.cpp lines 1009-1027
        min_rom_size = self._calculate_min_rom_size()

        # Expand ROM buffer if needed (mimics SSBFileInjector.exe behavior)
        current_size = len(self.n64.rom)
        if min_rom_size > current_size:
            # Expand in 4MB increments like the C++ version
            expanded_size = current_size
            while expanded_size < min_rom_size:
                expanded_size += 4 * 0x100000  # +4MB

            if on_progress:
                on_progress(
                    f"Expanding ROM from {current_size} to {expanded_size} bytes")

            # Extend with 0xFF padding (N64 unmapped space)
            self.n64.rom.extend(b'\xFF' * (expanded_size - current_size))

        ReplaceData(self.n64, "filetable.bin", new_tbl)

        count_delta = len(self.entries) - self._initial_count
        if count_delta:
            for ptr in NALEnum:
                process(self.n64, ptr, count_delta)

        FinishROM(self.n64, pad=True)

        # Pad ROM to 4096-byte alignment (matching SSBFileInjector.exe behavior)
        rom_data = bytes(self.n64.rom)
        rom_data = pad_rom_file(rom_data)

        os.makedirs(os.path.dirname(
            os.path.abspath(self._output_rom)), exist_ok=True)
        with open(self._output_rom, "wb") as f:
            f.write(rom_data)

        if on_progress:
            on_progress(f"Done - wrote {self._output_rom}")
        print(f"ROM written -> {self._output_rom}")
