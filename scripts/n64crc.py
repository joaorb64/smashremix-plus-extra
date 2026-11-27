#!/usr/bin/env python3
"""Recompute an N64 ROM's header CRC, replacing rn64crc.exe so Linux doesn't need wine.

The CIC is detected from the boot code (CIC-6101/6102/6103/6106; SSB64 is 6103).

Usage: n64crc.py [-u] rom.z64
  -u  write the new CRC into the ROM (otherwise it is only printed)
"""
import struct
import sys
import zlib

M = 0xFFFFFFFF
CRC_START = 0x1000
CRC_LENGTH = 0x100000
# boot code (0x40-0x1000) CRC32 -> (CIC name, seed)
CICS = {
    0x6170A4A1: ("6101", 0xF8CA4DDC),
    0x90BB6CB5: ("6102", 0xF8CA4DDC),
    0x0B050EE0: ("6103", 0xA3886759),
    0xACC8580A: ("6106", 0x1FEA617A),
}


def detect_cic(rom: bytes):
    return CICS.get(zlib.crc32(rom[0x40:0x1000]) & M)


def calc_crc(rom: bytes, cic: str, seed: int):
    t1 = t2 = t3 = t4 = t5 = t6 = seed
    data = rom[CRC_START:CRC_START + CRC_LENGTH]
    data += bytes(CRC_LENGTH - len(data))
    for d in struct.unpack(">%dI" % (CRC_LENGTH // 4), data):
        if (t6 + d) & M < t6:
            t4 = (t4 + 1) & M
        t6 = (t6 + d) & M
        t3 ^= d
        n = d & 0x1F
        r = ((d << n) | (d >> (32 - n))) & M
        t5 = (t5 + r) & M
        if t2 > d:
            t2 ^= r
        else:
            t2 ^= t6 ^ d
        t1 = (t1 + (t5 ^ d)) & M
    if cic == "6103":
        return ((t6 ^ t4) + t3) & M, ((t5 ^ t2) + t1) & M
    if cic == "6106":
        return (t6 * t4 + t3) & M, (t5 * t2 + t1) & M
    return (t6 ^ t4 ^ t3) & M, (t5 ^ t2 ^ t1) & M


def main(argv):
    update = "-u" in argv
    paths = [a for a in argv if a != "-u"]
    if len(paths) != 1:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    with open(paths[0], "rb") as f:
        rom = bytearray(f.read())
    if rom[:4] != b"\x80\x37\x12\x40":
        print("not a big-endian (.z64) ROM", file=sys.stderr)
        return 1
    cic = detect_cic(rom)
    if cic is None:
        print("unknown/unsupported CIC boot code", file=sys.stderr)
        return 1
    old = struct.unpack(">II", rom[0x10:0x18])
    crc1, crc2 = calc_crc(rom, *cic)
    print("CIC: " + cic[0])
    print("Old CRC: %08X %08X" % old)
    print("New CRC: %08X %08X" % (crc1, crc2))
    if update and (crc1, crc2) != old:
        with open(paths[0], "r+b") as f:
            f.seek(0x10)
            f.write(struct.pack(">II", crc1, crc2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
