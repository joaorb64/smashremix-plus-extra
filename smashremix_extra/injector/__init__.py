"""
SSB64 ROM file injector - self-contained package.

Only external dependency: SSB.py (N64/SSBtbl ROM utilities).

Usage:
    from smashremix_extra.injector import ROMInjector, MODIFIED_FILES
    from smashremix_extra.injector.vpk0 import compress
"""

from .injector import ROMInjector, MODIFIED_FILES, _parse_reqlist
from .vpk0 import compress as vpk0_compress
from .file_appender import pad_rom_file, pad_to_alignment

__all__ = [
    "ROMInjector",
    "MODIFIED_FILES",
    "vpk0_compress",
    "pad_rom_file",
    "pad_to_alignment",
]
