"""Smash Remix music instrument bank: which keys/velocities each instrument
can play.

The sequence player (n_seqplayer.c, __n_lookupSoundQuick at 0x8002E41C) looks
a note up by binary search over the instrument's sound keymaps and uses the
result without a NULL check, so a note on a channel with no instrument, or
whose key/velocity no sample covers, crashes the game.

Vanilla instruments (0x00-0x2A) are read from the original ROM's bank;
Smash Remix's additions from the add_instrument_sample() calls in midi.asm.
"""

import re
import struct
from typing import List, Optional, Tuple

KeyMap = Tuple[int, int, int, int]          # velMin, velMax, keyMin, keyMax
Instrument = Optional[List[KeyMap]]         # None = empty slot (crashes)

CTL_TABLE_PTR = 0x3D75C                     # ROM offset of the music .ctl address
VANILLA_COUNT = 0x2B


class Bank:
    def __init__(self, instruments: List[Instrument]):
        self.instruments = instruments

    def lookup(self, program: int, key: int, velocity: int) -> bool:
        """True if the game finds a sample for this note (same search as the game)."""
        maps = self.instruments[program] if program < len(self.instruments) else None
        if not maps:
            return False
        lo, hi = 1, len(maps)
        while hi >= lo:
            i = (lo + hi) // 2
            vmin, vmax, kmin, kmax = maps[i - 1]
            if kmin <= key <= kmax and vmin <= velocity <= vmax:
                return True
            if key < kmin or (velocity < vmin and key <= kmax):
                hi = i - 1
            else:
                lo = i + 1
        return False

    def is_valid(self, program: int) -> bool:
        return program < len(self.instruments) and bool(self.instruments[program])

    @property
    def max_program(self) -> int:
        return len(self.instruments) - 1


def _vanilla_instruments(rom: bytes) -> List[Instrument]:
    ctl = struct.unpack(">I", rom[CTL_TABLE_PTR:CTL_TABLE_PTR + 4])[0]
    bank = ctl + struct.unpack(">I", rom[ctl + 4:ctl + 8])[0]
    count = struct.unpack(">h", rom[bank:bank + 2])[0]
    offsets = struct.unpack(f">{count}I", rom[bank + 12:bank + 12 + 4 * count])

    instruments: List[Instrument] = []
    for offset in offsets:
        if not offset:
            instruments.append(None)
            continue
        inst = ctl + offset
        sound_count = struct.unpack(">h", rom[inst + 14:inst + 16])[0]
        sounds = struct.unpack(f">{sound_count}I", rom[inst + 16:inst + 16 + 4 * sound_count])
        maps = []
        for sound in sounds:
            keymap = ctl + struct.unpack(">I", rom[ctl + sound + 4:ctl + sound + 8])[0]
            maps.append(tuple(rom[keymap:keymap + 4]))
        instruments.append(maps)
    return instruments


def _remix_instruments(midi_asm: str) -> List[Instrument]:
    instruments: List[Instrument] = []
    samples: List[KeyMap] = []
    with open(midi_asm, "r", encoding="utf-8") as f:
        for line in f:
            line = line.split("//")[0]
            m = re.match(r"\s*add_instrument_sample\((.*)\)", line)
            if m:
                args = [a.strip() for a in m.group(1).split(",")]
                vel_min, vel_max, key_min, key_max = (int(a, 0) for a in args[6:10])
                samples.append((vel_min, vel_max, key_min, key_max))
            elif re.match(r"\s*add_instrument\(", line):
                instruments.append(samples)
                samples = []
    return instruments


def load_bank(rom_path: str, midi_asm_path: str) -> Bank:
    with open(rom_path, "rb") as f:
        rom = f.read()
    vanilla = _vanilla_instruments(rom)[:VANILLA_COUNT]
    return Bank(vanilla + _remix_instruments(midi_asm_path))
