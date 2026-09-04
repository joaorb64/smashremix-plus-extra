"""
Read / patch a stage's `MPGroundData` (lives inside header.bin) from config.yaml.
All edits are in-place scalar writes - nothing moves, no pointer is touched.

    # config.yaml (any subset; omit a key to leave it alone)
    blast_zones:        { top: 5700, bottom: -1500, left: -6000, right: 6000 }
    camera_bounds:      { top: 3900, bottom: -1500, left: -4200, right: 4200 }
    blast_zones_team:   { top: 5700, bottom: -1500, left: -6000, right: 6000 }
    camera_bounds_team: { top: 3900, bottom: -1500, left: -4200, right: 4200 }
    light_angle:        [80, 25]           # [pitch, yaw] (or [pitch, yaw, roll]) deg
    fog:               { color: [225, 200, 255], alpha: 0 }  # color = #RRGGBB or [r,g,b]
    emblem_colors:     [[255,0,0], [0,0,255], [255,255,0], [0,255,0]]   # per-player
    alt_warning:        -3200              # whistle plays below this altitude
    zoom_start:         [0, 0, 0]          # bonus-stage pause camera, region start (Vec3, s16)
    zoom_end:           [0, 0, 0]          # bonus-stage pause camera, region end
    layer_mask:         0b0001             # geo-layer render mask (bit set = secondary 2-DL path)

`MPGroundData` sits at `offsets.header[1]` inside header.bin. Field offsets below
are relative to it; the layout is verified against ssb-decomp `mptypes.h` and a
real ShadowMoses header.bin.

    +0x44  layer_mask (u8)
    +0x4C  fog_color (u8 r,g,b)     +0x4F  fog_alpha (u8)
    +0x50  emblem_colors[4]  (u8 r,g,b each)
    +0x60  light_angle x,y (f32)    +0x68  camera_tilt (f32 rad; see below)
    +0x6C  camera_bound      top/bottom/right/left  (s16 x4)
    +0x74  map_bound  (= blast zones)  top/bottom/right/left  (s16 x4)
    +0x88  alt_warning (s16)
    +0x8A  camera_bound_team  top/bottom/right/left  (s16 x4)
    +0x92  map_bound_team     top/bottom/right/left  (s16 x4)
    +0x9A  zoom_start (s16 x,y,z)    +0xA0  zoom_end (s16 x,y,z)
           ^ NOT a KO zoom. Fed to gmCameraSetStatusMapZoom() only by
           ifCommonBattleGoUpdateInterface when you pause a BONUS game
           (Break the Targets / Board the Platforms): the pause camera pulls
           back and frames the map from zoom_start to zoom_end. Unused on
           normal VS stages.

`light_angle` is a Vec3f but only x/y are scene lighting (pitch/yaw deg). The
3rd component (+0x68) is actually the **camera baseline pitch**: gmcamera.c's
`gmCameraGetAdjustAtAngle` adds `groundData->light_angle.z` (radians) to the
camera's vertical look angle every frame. Every vanilla stage stores
-0.17453294 = -10 deg. It is exposed here as `camera_tilt`, in DEGREES
(converted to radians on write). More negative = camera looks further down at
the stage. This is a pitch, not a Z-axis roll.
"""
from __future__ import annotations

import math
import struct

_BSIDES = {"top": 0, "bottom": 2, "right": 4, "left": 6}

# key -> (offset relative to MPGroundData, kind). Order is the dump order.
_FIELDS = (
    ("layer_mask",         0x44, "bin8"),  # bit per gr_desc geo layer
    ("fog",                0x4C, "fog"),
    ("emblem_colors",      0x50, "rgb4"),
    ("light_angle",        0x60, "vec2f"),   # scene light pitch/yaw, degrees
    ("camera_tilt",        0x68, "angle_deg"),   # camera baseline pitch, degrees
    ("camera_bounds",      0x6C, "bounds"),
    ("blast_zones",        0x74, "bounds"),
    ("alt_warning",        0x88, "s16"),
    ("camera_bounds_team", 0x8A, "bounds"),
    ("blast_zones_team",   0x92, "bounds"),
    ("zoom_start",         0x9A, "vec3h"),
    ("zoom_end",           0xA0, "vec3h"),
)
_STRUCT_END = 0xA6      # last touched byte + a little; used as a size sanity check


def _color(v):
    if isinstance(v, str):
        v = v.lstrip("#")
        return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
    return tuple(int(c) & 0xFF for c in v[:3])


# --------------------------------------------------------------------- read
def _read_field(d, b, off, kind):
    if kind == "u8":
        return d[b + off]
    if kind == "bin8":
        return f"0b{d[b + off]:04b}"
    if kind == "s16":
        return struct.unpack_from(">h", d, b + off)[0]
    if kind == "u32":
        return struct.unpack_from(">I", d, b + off)[0]
    if kind == "bounds":
        return {s: struct.unpack_from(">h", d, b + off + so)[0]
                for s, so in _BSIDES.items()}
    if kind == "vec2f":
        return list(struct.unpack_from(">2f", d, b + off))
    if kind == "vec3f":
        return list(struct.unpack_from(">3f", d, b + off))
    if kind == "angle_deg":
        return round(math.degrees(struct.unpack_from(">f", d, b + off)[0]), 4)
    if kind == "vec3h":
        return list(struct.unpack_from(">3h", d, b + off))
    if kind == "rgb4":
        return [list(d[b + off + i * 3:b + off + i * 3 + 3]) for i in range(4)]
    if kind == "fog":
        return {"color": list(d[b + off:b + off + 3]), "alpha": d[b + off + 3]}
    raise ValueError(kind)


def read(header_bytes, groupdata_offset):
    """Every editable MPGroundData field -> dict, in `_FIELDS` order.
    Back-compat: `camera_bounds`, `blast_zones`, `light_angle`, `fog` are the
    keys the draw tool already uses."""
    d, b = header_bytes, groupdata_offset
    return {key: _read_field(d, b, off, kind) for key, off, kind in _FIELDS}


# -------------------------------------------------------------------- write
def _write_field(d, b, off, kind, val):
    if kind == "u8":
        d[b + off] = int(val) & 0xFF
    elif kind == "bin8":
        d[b + off] = (int(val, 0) if isinstance(val, str) else int(val)) & 0xFF
    elif kind == "s16":
        struct.pack_into(">h", d, b + off, int(val))
    elif kind == "u32":
        struct.pack_into(">I", d, b + off, int(val) & 0xFFFFFFFF)
    elif kind == "bounds":
        for side, so in _BSIDES.items():
            if side in val:
                struct.pack_into(">h", d, b + off + so, int(val[side]))
    elif kind == "vec2f":
        for i, c in enumerate(val[:2]):
            struct.pack_into(">f", d, b + off + i * 4, float(c))
    elif kind == "vec3f":
        for i, c in enumerate(val[:3]):
            struct.pack_into(">f", d, b + off + i * 4, float(c))
    elif kind == "angle_deg":
        struct.pack_into(">f", d, b + off, math.radians(float(val)))
    elif kind == "vec3h":
        for i, c in enumerate(val[:3]):
            struct.pack_into(">h", d, b + off + i * 2, int(c))
    elif kind == "rgb4":
        for i, c in enumerate(val[:4]):
            d[b + off + i * 3:b + off + i * 3 + 3] = bytes(_color(c))
    elif kind == "fog":
        val = val or {}
        if "color" in val:
            d[b + off:b + off + 3] = bytes(_color(val["color"]))
        if "alpha" in val:
            d[b + off + 3] = int(val["alpha"]) & 0xFF
    else:
        raise ValueError(kind)


def apply(header_path, groupdata_offset, cfg, *, label=""):
    """`cfg` = the whole config.yaml dict; writes any `_FIELDS` key present in
    it. Returns the list of keys it changed."""
    label = f"{label}: " if label else ""
    present = [(k, o, t) for k, o, t in _FIELDS if cfg.get(k) is not None]
    if not present:
        return []

    d = bytearray(open(header_path, "rb").read())
    b = groupdata_offset
    if b + _STRUCT_END > len(d):
        raise ValueError(f"{label}header.bin too small for MPGroundData @ 0x{b:X}")

    for key, off, kind in present:
        _write_field(d, b, off, kind, cfg[key])

    open(header_path, "wb").write(d)
    return [k for k, _, _ in present]
