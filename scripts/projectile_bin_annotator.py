#!/usr/bin/env python3
"""
Read-only annotator for extra-character projectile "gfx" and "hitbox" bin
files (e.g. extra_characters/<Character>/fsmash_gfx.bin / fsmash_hitbox.bin).

These files are raw serialized SSB64 engine structs (MObj/MObjSub/AObjEvent32
animation scripts, WPAttributes hitbox data) plus the game's own in-file
pointer-relocation chain format. This tool walks that chain and tries to
auto-classify what each chain node points at (palette / sprite array /
animation script / display list), so a human can confirm the layout before
building a generator on top of it.

Format background (reverse engineered against VetriTheRetri/ssb-decomp-re,
src/sys/objtypes.h, src/sys/objdef.h, src/wp/wptypes.h):

  Self-relocation chain: a linked list of 4-byte nodes starting at a file's
  "InternalFileTableOffsetBytes" (see extra_characters/*/config.yaml). Each
  node is two big-endian u16s: [next_word_index][target_word_index]. A
  reader multiplies both by 4 to get byte offsets. next_word_index == 0xFFFF
  marks the end of the chain. target_word_index == 0 means "unused / not a
  pointer" (skip). This chain is how the game's file loader turns
  ROM-relative word indices into real pointers at load time; here it
  doubles as a map of every real pointer *field* in the file.

  AObjEvent32 (animation script) command word, MSB-first:
      opcode:7  flags:10  payload:15
  See OPCODES / MAT_TRACK_BITS below for the concrete values, lifted from
  AObjEvent32Kind / AOBJ_MATFLAG_* in objdef.h.

Usage:
    python3 scripts/projectile_bin_annotator.py gfx <file.bin> [--table-offset 0x58]
    python3 scripts/projectile_bin_annotator.py hitbox <file.bin> [--reqlist file_reqlist.txt]
"""

import argparse
import struct
import sys

OPCODES = {
    0: "End", 1: "Jump", 2: "Wait", 3: "SetValBlock", 4: "SetVal",
    5: "SetValRateBlock", 6: "SetValRate", 7: "SetTargetRate",
    8: "SetVal0RateBlock", 9: "SetVal0Rate", 10: "SetValAfterBlock",
    11: "SetValAfter", 12: "Cmd12", 13: "SetInterp", 14: "SetAnim",
    15: "SetFlags", 16: "Cmd16", 17: "Cmd17", 18: "SetExtValAfterBlock",
    19: "SetExtValAfter", 20: "SetExtValBlock", 21: "SetExtVal",
    22: "Cmd22", 23: "Cmd23",
}

# AOBJ_MATFLAG_* bit assignments (bit N -> nGCAnimTrackMaterialStart + N)
MAT_TRACK_BITS = {
    0: "TEXID", 1: "TRAU", 2: "TRAV", 3: "SCAU", 4: "SCAV",
    5: "TEXIDNEXT", 6: "SCRU", 7: "SCRV", 8: "LFRAC", 9: "PALETTEID",
}

# Opcodes whose command word is immediately followed by one f32/u32 payload
# word per set flag bit (only single-bit TEXID/PALETTEID scripts are common
# in these 2D sprite projectiles, so we only special-case the 1-bit case).
VALUE_FOLLOWS = {3, 4, 5, 6, 7, 8, 9, 10, 11}


def u16(data, off):
    return int.from_bytes(data[off:off + 2], "big")


def u32(data, off):
    return int.from_bytes(data[off:off + 4], "big")


def f32(data, off):
    return struct.unpack(">f", data[off:off + 4])[0]


def walk_chain(data, start, max_nodes=512):
    """Walk the self-relocation chain starting at byte offset `start`.

    Returns a list of (field_addr, next_raw, target_word_idx, target_addr).
    Stops on 0xFFFF next-sentinel, out-of-bounds, or a repeated address.
    """
    nodes = []
    current = start
    seen = set()
    for _ in range(max_nodes):
        if current in seen or current + 4 > len(data):
            break
        seen.add(current)
        next_raw = u16(data, current)
        target_word = u16(data, current + 2)
        target_addr = target_word * 4
        nodes.append((current, next_raw, target_word, target_addr))
        if next_raw == 0xFFFF:
            break
        current = next_raw * 4
    return nodes


def decode_command(word):
    opcode = (word >> 25) & 0x7F
    flags = (word >> 15) & 0x3FF
    payload = word & 0x7FFF
    return opcode, flags, payload


def try_decode_matanim_script(data, off, max_words=64):
    """Attempt to decode an AObjEvent32 MatAnimJoint script at `off`.

    Returns a list of description strings if this looks like a plausible
    script (recognized opcodes throughout), else None.
    """
    lines = []
    pos = off
    for _ in range(max_words):
        if pos + 4 > len(data):
            return None
        word = u32(data, pos)
        opcode, flags, payload = decode_command(word)
        if opcode not in OPCODES:
            return None
        name = OPCODES[opcode]
        if opcode == 0:
            lines.append(f"  0x{pos:04X}: End")
            return lines
        if opcode in VALUE_FOLLOWS:
            track_bits = [MAT_TRACK_BITS.get(b, f"bit{b}")
                          for b in range(10) if flags & (1 << b)]
            pos += 4
            if pos + 4 > len(data):
                return None
            value = f32(data, pos)
            lines.append(
                f"  0x{pos - 4:04X}: {name:16} track={','.join(track_bits) or '-':10} "
                f"payload(dur)={payload:<5} -> value_word @0x{pos:04X} = {value}"
            )
            pos += 4
        else:
            lines.append(
                f"  0x{pos:04X}: {name:16} flags=0x{flags:03X} payload={payload}")
            pos += 4
        if len(lines) > 40:
            return None
    return None


def guess_ci4_dims(pixel_count):
    for w in (8, 16, 24, 32, 48, 64, 96, 128):
        if pixel_count % w == 0:
            h = pixel_count // w
            if max(w, h) / min(w, h) <= 4:
                return w, h
    return None, None


def annotate_gfx(path, table_offset):
    data = open(path, "rb").read()
    print(f"=== {path} ({len(data)} bytes) ===\n")

    print(
        f"-- Self-relocation chain (file-table), start=0x{table_offset:04X} --")
    nodes = walk_chain(data, table_offset)
    for field_addr, next_raw, target_word, target_addr in nodes:
        note = ""
        if target_word == 0:
            note = "(unused / null)"
        print(f"  field@0x{field_addr:04X}  next_raw=0x{next_raw:04X}  "
              f"-> target=0x{target_addr:04X} {note}")

    # Group consecutive field addresses (stride 4) with evenly-spaced,
    # non-null targets: this is the classic MObjSub.sprites pointer array.
    print("\n-- Candidate sprite/pointer arrays (consecutive 4-byte fields, evenly spaced targets) --")
    by_addr = sorted(n for n in nodes if n[2] != 0)
    i = 0
    while i < len(by_addr):
        run = [by_addr[i]]
        j = i + 1
        while (j < len(by_addr)
               and by_addr[j][0] == run[-1][0] + 4):
            run.append(by_addr[j])
            j += 1
        if len(run) >= 2:
            targets = [n[3] for n in run]
            diffs = {targets[k + 1] - targets[k]
                     for k in range(len(targets) - 1)}
            spacing = diffs.pop() if len(diffs) == 1 else None
            print(f"  fields 0x{run[0][0]:04X}..0x{run[-1][0]:04X} "
                  f"({len(run)} entries) -> targets {[hex(t) for t in targets]}"
                  + (f"  [even spacing 0x{spacing:X} bytes]" if spacing else ""))
            if spacing:
                w, h = guess_ci4_dims(spacing * 2)  # CI4 = 2 px/byte
                if w:
                    print(f"    -> plausible CI4 sprite size guess: {w}x{h} "
                          f"({spacing} bytes/frame)")
        i = j

    print("\n-- Scanning chain targets for animation scripts (AObjEvent32) --")
    seen_targets = set()
    for _, _, target_word, target_addr in nodes:
        if target_word == 0 or target_addr in seen_targets:
            continue
        seen_targets.add(target_addr)
        script = try_decode_matanim_script(data, target_addr)
        if script:
            print(f"  target=0x{target_addr:04X} decodes as a MatAnim script:")
            print("\n".join(script))

    print(f"\n-- Palette guess (if this file follows the 0x30 convention) --")
    pal_off = 0x30
    print(f"  16x RGBA5551 @ 0x{pal_off:02X}:")
    for i in range(16):
        off = pal_off + i * 2
        val = u16(data, off)
        r, g, b, a = (val >> 11) & 0x1F, (val >>
                                          6) & 0x1F, (val >> 1) & 0x1F, val & 1
        print(
            f"    [{i:2}] 0x{val:04X}  R{r*255//31:3} G{g*255//31:3} B{b*255//31:3} A{a}")


def annotate_hitbox(path, reqlist_path=None):
    data = open(path, "rb").read()
    print(f"=== {path} ({len(data)} bytes) - WPAttributes (src/wp/wptypes.h) ===\n")

    labels = []
    if reqlist_path:
        with open(reqlist_path) as f:
            labels = [l.strip().strip("${}") for l in f
                      if l.strip() and not l.startswith("END OF")]

    field_names = {0x00: "data (DL or DObjDesc*)", 0x04: "p_mobjsubs",
                   0x08: "anim_joints", 0x0C: "p_matanim_joints"}
    print("-- Pointer fields, walked via the resource chain (starts @0x00) --")
    print("  Note: this chain is walked via next_raw, NOT flat file position --")
    print("  fields it skips (e.g. anim_joints for a static-joint weapon) stay NULL")
    print("  and consume no reqlist entry.\n")
    nodes = walk_chain(data, 0x00)
    for i, (field_addr, next_raw, target_word, target_addr) in enumerate(nodes):
        name = field_names.get(field_addr, f"@0x{field_addr:02X}")
        label = labels[i] if i < len(labels) else "(no reqlist entry / unused)"
        print(f"  0x{field_addr:02X} {name:24} next_raw=0x{next_raw:04X} "
              f"value_raw=0x{target_word:04X} -> resource #{i}: {label}")
    visited = {n[0] for n in nodes}
    for off, name in field_names.items():
        if off not in visited:
            print(
                f"  0x{off:02X} {name:24} (not part of the chain -- stays NULL/unused)")

    print(
        "\n-- attack_offsets[2] (Vec3h, joint-relative hitbox positions) @0x10 --")
    for i in range(2):
        off = 0x10 + i * 6
        x = int.from_bytes(data[off:off+2], "big", signed=True)
        y = int.from_bytes(data[off+2:off+4], "big", signed=True)
        z = int.from_bytes(data[off+4:off+6], "big", signed=True)
        print(f"  [{i}] x={x} y={y} z={z}")

    print("\n-- map collision box @0x1C --")
    top, center, bottom, width = (
        int.from_bytes(data[0x1C+k*2:0x1C+k*2+2], "big", signed=True) for k in range(4)
    )
    print(f"  top={top} center={center} bottom={bottom} width={width}")

    print("\n-- bitfield tail @0x24-0x40 (damage/angle/knockback/etc, WPAttributes) --")
    print("  Packing verified against decomp's dMarioSpecial1_Fireball_WeaponAttributes:")
    print("  MSB-first, new 32-bit word only on overflow (no signed/unsigned split).")
    print("  Real content is 4 words (0x24-0x34); 0x34-0x40 is DMA-alignment padding.\n")
    fields = decode_wpattributes_bitfields(data)
    for name, val in fields.items():
        print(f"  {name:18} = {val}")


def _bits(word, width=32):
    return format(word, f"0{width}b")


def _take(bitstring, pos, width, signed=False):
    chunk = bitstring[pos:pos + width]
    val = int(chunk, 2)
    if signed and chunk[0] == "1":
        val -= (1 << width)
    return val, pos + width


def decode_wpattributes_bitfields(data):
    """Decode the WPAttributes bitfield tail (offset 0x24) into a dict.

    Verified against decomp's dMarioSpecial1_Fireball_WeaponAttributes by
    diffing an extra-character hitbox.bin that's a near-direct copy of
    Mario's fireball data against the known source values. Model: MSB-first
    packing, new 32-bit word only on bit overflow, 4 words of real content
    followed by zero padding up to the 64-byte (DMA-aligned) struct size.
    """
    w = [int.from_bytes(data[0x24 + i * 4:0x28 + i * 4], "big")
         for i in range(4)]
    b = [_bits(x) for x in w]
    out = {}

    pos = 0
    out["size"], pos = _take(b[0], pos, 16)
    out["angle"], pos = _take(b[0], pos, 10, signed=True)

    pos = 0
    out["knockback_scale"], pos = _take(b[1], pos, 10)
    out["damage"], pos = _take(b[1], pos, 8)
    out["element"], pos = _take(b[1], pos, 4)
    out["knockback_weight"], pos = _take(b[1], pos, 10)

    pos = 0
    out["shield_damage"], pos = _take(b[2], pos, 8, signed=True)
    out["attack_count"], pos = _take(b[2], pos, 2)
    out["can_setoff"], pos = _take(b[2], pos, 1)
    out["sfx"], pos = _take(b[2], pos, 10)
    out["priority"], pos = _take(b[2], pos, 3)
    out["can_rehit_item"], pos = _take(b[2], pos, 1)
    out["can_rehit_fighter"], pos = _take(b[2], pos, 1)
    out["can_hop"], pos = _take(b[2], pos, 1)
    out["can_reflect"], pos = _take(b[2], pos, 1)
    out["can_absorb"], pos = _take(b[2], pos, 1)
    out["can_shield"], pos = _take(b[2], pos, 1)
    out["unused_0x2F_b6"], pos = _take(b[2], pos, 1)

    pos = 0
    out["unused_0x2F_b7"], pos = _take(b[3], pos, 1)
    out["knockback_base"], pos = _take(b[3], pos, 10)

    return out


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_gfx = sub.add_parser(
        "gfx", help="Annotate a projectile gfx.bin (palette/anim/sprites/DL)")
    p_gfx.add_argument("file")
    p_gfx.add_argument("--table-offset", default="0x58",
                       help="InternalFileTableOffsetBytes from config.yaml (default: 0x58)")

    p_hit = sub.add_parser(
        "hitbox", help="Annotate a projectile hitbox.bin (WPAttributes)")
    p_hit.add_argument("file")
    p_hit.add_argument("--reqlist", default=None,
                       help="Matching *_hitbox_reqlist.txt, to label pointer targets")

    args = parser.parse_args()
    if args.cmd == "gfx":
        annotate_gfx(args.file, int(args.table_offset, 16))
    elif args.cmd == "hitbox":
        annotate_hitbox(args.file, args.reqlist)


if __name__ == "__main__":
    sys.exit(main())
