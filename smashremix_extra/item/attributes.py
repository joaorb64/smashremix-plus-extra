"""
ITAttributes (item footer) read/write, field layout from ssb-decomp-re ittypes.h
(IDO big-endian bitfield packing; verified against every vanilla item in file 0xFB).

    from smashremix_extra.item import attributes
    attributes.apply(data, offset, {"damage": 4, "weight": "light", ...})
"""
import struct

# name: (word offset, highest bit, bit count, signed)
BITFIELDS = {
    "is_display_xlu": (0x10, 31, 1, False),
    "is_item_dobjs": (0x10, 30, 1, False),
    "is_display_colanim": (0x10, 29, 1, False),
    "is_give_hitlag": (0x10, 28, 1, False),
    "weight": (0x10, 27, 1, False),                 # 1 = light
    "attack_offset0_x": (0x10, 26, 16, True),
    "attack_offset0_y": (0x14, 31, 16, True),
    "attack_offset0_z": (0x14, 15, 16, True),
    "attack_offset1_x": (0x18, 31, 16, True),
    "attack_offset1_y": (0x18, 15, 16, True),
    "attack_offset1_z": (0x1C, 31, 16, True),
    "angle": (0x34, 31, 10, True),
    "knockback_scale": (0x34, 21, 10, False),
    "damage": (0x34, 11, 8, False),
    "element": (0x34, 3, 4, False),
    "knockback_weight": (0x38, 31, 10, False),
    "shield_damage": (0x38, 21, 8, True),
    "attack_count": (0x38, 13, 2, False),
    "can_setoff": (0x38, 11, 1, False),
    "hit_sfx": (0x38, 10, 10, False),
    "priority": (0x3C, 31, 3, False),
    "can_rehit_item": (0x3C, 28, 1, False),
    "can_rehit_fighter": (0x3C, 27, 1, False),
    "can_hop": (0x3C, 26, 1, False),
    "can_reflect": (0x3C, 25, 1, False),
    "can_shield": (0x3C, 24, 1, False),
    "knockback_base": (0x3C, 23, 10, False),
    "type": (0x3C, 13, 4, False),
    "hitstatus": (0x3C, 9, 4, False),
    "drop_sfx": (0x40, 31, 10, False),
    "throw_sfx": (0x40, 21, 10, False),
    "smash_sfx": (0x40, 11, 10, False),
    "vel_scale": (0x44, 31, 9, False),
}
# name: (offset, struct format)
PLAIN = {
    "hurtbox_offset": (0x1E, "3h"),
    "hurtbox_size": (0x24, "3h"),
    "map_coll": (0x2A, "4h"),                       # top, center, bottom, width
    "size": (0x32, "H"),                            # hitbox diameter
    "spin_speed": (0x46, "H"),
}
SIZE = 0x48

TYPES = {"melee": 1, "shoot": 2, "throw": 3, "collision": 4, "other": 5}
WEIGHTS = {"heavy": 0, "light": 1}
MAP_COLL_KEYS = ("top", "center", "bottom", "width")


def read(data, offset=0):
    out = {}
    for name, (word, hi, n, signed) in BITFIELDS.items():
        v = struct.unpack_from(">I", data, offset + word)[0] >> (hi - n + 1) & ((1 << n) - 1)
        if signed and v >> (n - 1):
            v -= 1 << n
        out[name] = v
    for name, (off, fmt) in PLAIN.items():
        v = struct.unpack_from(">" + fmt, data, offset + off)
        out[name] = list(v) if len(v) > 1 else v[0]
    return out


def _value(name, v):
    if name == "type" and isinstance(v, str):
        return TYPES[v.lower()]
    if name == "weight" and isinstance(v, str):
        return WEIGHTS[v.lower()]
    if isinstance(v, bool):
        return int(v)
    return v


def apply(data, offset, values):
    """Writes `values` (config.yaml attributes:, minus file/offset) into `data`
    (a bytearray) at the ITAttributes at `offset`."""
    if offset + SIZE > len(data):
        raise ValueError(f"ITAttributes at 0x{offset:X} past the end of the file (0x{len(data):X})")
    for name, v in values.items():
        if name in ("file", "offset"):
            continue
        if name in BITFIELDS:
            word, hi, n, signed = BITFIELDS[name]
            v = _value(name, v)
            lo = -(1 << (n - 1)) if signed else 0
            hi_v = (1 << (n - 1)) - 1 if signed else (1 << n) - 1
            if not lo <= v <= hi_v:
                raise ValueError(f"attributes.{name} = {v} doesn't fit in {n} bits")
            shift = hi - n + 1
            mask = ((1 << n) - 1) << shift
            cur = struct.unpack_from(">I", data, offset + word)[0]
            struct.pack_into(">I", data, offset + word, (cur & ~mask) | ((v << shift) & mask))
        elif name in PLAIN:
            off, fmt = PLAIN[name]
            if name == "map_coll" and isinstance(v, dict):
                cur = struct.unpack_from(">4h", data, offset + off)
                v = [v.get(k, c) for k, c in zip(MAP_COLL_KEYS, cur)]
            if not isinstance(v, (list, tuple)):
                v = [v]
            struct.pack_into(">" + fmt, data, offset + off, *v)
        else:
            raise ValueError(f"unknown item attribute {name!r} (known: "
                             f"{', '.join(list(BITFIELDS) + list(PLAIN))})")
