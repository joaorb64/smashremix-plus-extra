"""
SSB64 stage map-collision ("clip" / yakumono) - decode, edit, and rebuild the
`MPGeometryData` in a stage.bin.

The stage's config.yaml carries a `collision:` LIST, one entry per line:

    collision:
      - {group: 1, floor: [-2100,1807, 2100,1807], flags: [drop_through]}
      - {group: 2, rwall: [-2100,-120, -2100,1807]}
      - {group: 6, floor: [2100,1807, 2700,1807, 7500,1807]}   # polyline

`group:` is the yakumono id hazards.asm toggles; then exactly one of
floor / ceil / lwall / rwall holding a flat x,y,x,y,... list (two points for a
straight line, more for a polyline). `flags:` is optional - `drop_through`,
`ledge`, and/or a surface-index int. `point_flags:` (optional, one int per point:
0x8000 ledge, 0x4000 drop-through, low byte surface) sets them per point instead -
the game reads a segment's flags from its first point, so one polyline can mix
(vanilla stages do).

`apply()` (run by the appender for a config.yaml `collision:` key) rebuilds the
4 arrays MPGeometryData points at, APPENDS them to stage.bin, and repoints those
4 self-relocation-chain nodes (their links are left alone, so the loader still
relocates them). header.bin and the MPGeometryData struct do not move. Points
at the same (x,y) AND flags weld to one vertex so lines can share an index;
disconnected groups / lone endpoints are fine on a stage. A stage supports at
most 6 collision groups (`_MAX_YAKUMONO_GROUPS`).
"""
from __future__ import annotations

import struct

from smashremix_extra.logger import logger

_LINE_TYPES = ("floor", "ceil", "rwall", "lwall")   # MPLineData[0..3] order
_FLAG_LEDGE = 0x8000
_FLAG_DROP = 0x4000
_FLAG_NAMES = {
    "drop_through": _FLAG_DROP, "drop_thru": _FLAG_DROP, "drop": _FLAG_DROP,
    "pass_through": _FLAG_DROP, "platform": _FLAG_DROP,
    "ledge": _FLAG_LEDGE, "grab": _FLAG_LEDGE, "ledge_grab": _FLAG_LEDGE,
}
_CHAIN_END = 0x3FFFC
_DENTRY = struct.pack(">ii9f", 1, 0, 0, 0, 0, 0, 0, 0, 1.0, 1.0, 1.0)  # 0x2C
_DTERM = struct.pack(">i", 0x12) + b"\0" * 0x28                        # id 0x12
_DOBJ_ARRAY_MAX = 18
# Hard ceiling on collision groups. GoldEditor caps stage collision at 6, and so
# does Remix's stage-object loader: growing the collision-layer DObjDesc list
# past 6 makes STAGE_OBJECT_INIT_ build a joint tree longer than the parallel
# DL-pointer array that 0x805697bc allocates, and gcAttachDLsToDObjTree
# (0x8000F8F4) walks off the end -> load crash at 0x8000F930. Verified in-game.
_MAX_YAKUMONO_GROUPS = 6

MAPOBJ_KIND = {
    0x00: "P1 start", 0x01: "P2 start", 0x02: "P3 start", 0x03: "P4 start",
    0x04: "item spawn", 0x05: "scale L", 0x06: "scale R", 0x07: "pakkun L",
    0x08: "pakkun R", 0x09: "power block", 0x0A: "pipe L", 0x0B: "pipe R",
    0x0C: "acid", 0x0D: "twister", 0x0E: "monster", 0x13: "bumper",
    0x14: "pipe wall", 0x15: "movie player 1", 0x16: "movie player 2",
    0x17: "movie player 3", 0x18: "demo P1", 0x19: "demo P2", 0x1A: "demo P3",
    0x1B: "demo P4", 0x1C: "demo P5", 0x1D: "demo P6", 0x1E: "demo P7",
    0x1F: "demo P8", 0x20: "rebirth platform", 0x21: "1P player",
    0x22: "1P ally 1", 0x23: "1P ally 2", 0x24: "1P ally 3", 0x25: "1P enemy 1",
    0x26: "1P enemy 2", 0x27: "1P enemy 3", 0x28: "1P enemy 4",
    0x29: "bonus3 taru bomb", 0x2B: "1P enemy team", 0x2C: "challenger player",
}


# --------------------------------------------------------------------------- io
def _s16(d, o):
    return struct.unpack_from(">h", d, o)[0]


def _u16(d, o):
    return struct.unpack_from(">H", d, o)[0]


def _u32(d, o):
    return struct.unpack_from(">I", d, o)[0]


def _w16(d, o, v):
    struct.pack_into(">H", d, o, v & 0xFFFF)


def _walk_chain(d, head):
    out, off, seen = [], head, set()
    while 0 <= off < len(d) - 3 and off not in seen:
        seen.add(off)
        nxt = _u16(d, off) * 4
        out.append((off, _u16(d, off + 2) * 4))
        if nxt in (_CHAIN_END, 0) or nxt >= len(d):
            break
        off = nxt
    return out


def find_chain_head(d, min_nodes=8):
    """Brute-scan for the fixup chain head (the offset that walks the most valid
    nodes). Use when the config.yaml `offsets.stage[0]` isn't handy."""
    best, best_n = None, 0
    for off in range(0, min(len(d), 0x20000), 4):
        n = len(_walk_chain(d, off))
        if n > best_n:
            best, best_n = off, n
    return best if best_n >= min_nodes else None


def _find_geometry(d, fields):
    """MPGeometryData: u16 count, then pointer fields at +4/+8/+C/+10 (ascending
    targets) and +0x18, all of which are fixup nodes."""
    fset = {f for f, _ in fields}
    for base in range(0, len(d) - 0x1C, 4):
        if not all((base + o) in fset for o in (0x04, 0x08, 0x0C, 0x10, 0x18)):
            continue
        t = [(_u32(d, base + o) & 0xFFFF) * 4 for o in (0x04, 0x08, 0x0C, 0x10)]
        if t[0] < t[1] < t[2] < t[3] and 1 <= _u16(d, base) <= 32:
            return base
    return None


# ------------------------------------------------------------------------ decode
class Geometry:
    """Decoded MPGeometryData. Coordinates are game space (y up)."""

    def __init__(self, geom_off, group_count, verts, vids, links, groups, mapobjs):
        self.geom_off = geom_off
        self.group_count = group_count
        self.verts = verts        # [(x, y, flags)]
        self.vids = vids          # flat vertex-index list
        self.links = links        # [(first_vid_index, vertex_count)] per line
        self.groups = groups      # [(yakumono_id, [(first_line, line_count)] * 4)]
        self.mapobjs = mapobjs    # [(kind, x, y)]

    def line_points(self, line_id):
        first, cnt = self.links[line_id]
        return [self.verts[self.vids[k]] for k in range(first, first + cnt)
                if self.vids[k] < len(self.verts)]

    def iter_lines(self):
        """-> (yakumono_id, type_name, line_id, [(x, y, flags), ...])."""
        for yid, per_type in self.groups:
            for ti, (first, cnt) in enumerate(per_type):
                for ln in range(first, first + cnt):
                    yield yid, _LINE_TYPES[ti], ln, self.line_points(ln)

    def gaps(self):
        """Line endpoints touched by no other line (informational only)."""
        touch: dict = {}
        for first, cnt in self.links:
            for k in (first, first + cnt - 1):
                v = self.vids[k]
                touch[v] = touch.get(v, 0) + 1
        return sorted({(self.verts[v][0], self.verts[v][1])
                       for v, n in touch.items() if n < 2})


def decode(d, chain_head=None):
    d = bytes(d)
    if chain_head is None:
        chain_head = find_chain_head(d)
        if chain_head is None:
            raise ValueError("could not find the stage.bin fixup chain")
    fields = _walk_chain(d, chain_head)
    geom = _find_geometry(d, fields)
    if geom is None:
        raise ValueError("could not locate MPGeometryData in stage.bin")

    yk = _u16(d, geom)
    p_vtx, p_vid, p_lnk, p_lni = (
        (_u32(d, geom + o) & 0xFFFF) * 4 for o in (0x04, 0x08, 0x0C, 0x10))
    mo_cnt = _u16(d, geom + 0x14)
    p_mo = (_u32(d, geom + 0x18) & 0xFFFF) * 4

    tgts = sorted({t for _, t in fields} | {geom, len(d)})

    def upto(p):
        return next(t for t in tgts if t > p)

    verts = [(_s16(d, p_vtx + i * 6), _s16(d, p_vtx + i * 6 + 2),
              _u16(d, p_vtx + i * 6 + 4))
             for i in range((upto(p_vtx) - p_vtx) // 6)]
    vids = [_u16(d, p_vid + i * 2) for i in range((upto(p_vid) - p_vid) // 2)]
    links = [(_u16(d, p_lnk + i * 4), _u16(d, p_lnk + i * 4 + 2))
             for i in range((upto(p_lnk) - p_lnk) // 4)]
    groups = [(_u16(d, p_lni + g * 18),
               [(_u16(d, p_lni + g * 18 + 2 + t * 4),
                 _u16(d, p_lni + g * 18 + 4 + t * 4)) for t in range(4)])
              for g in range(yk)]
    mapobjs = [(_u16(d, p_mo + i * 6), _s16(d, p_mo + i * 6 + 2),
                _s16(d, p_mo + i * 6 + 4)) for i in range(mo_cnt)]
    return Geometry(geom, yk, verts, vids, links, groups, mapobjs)


def flag_names(f):
    out = []
    if f & _FLAG_DROP:
        out.append("drop_through")
    if f & _FLAG_LEDGE:
        out.append("ledge")
    if f & 0xFF:
        out.append(int(f & 0xFF))
    return out


def to_spec(geo):
    """A decoded Geometry -> the config.yaml `collision:` list."""
    out = []
    for yid, tname, _ln, pts in geo.iter_lines():
        flat = [c for (x, y, _f) in pts for c in (x, y)]
        entry = {"group": yid, tname: flat}
        names = flag_names(pts[0][2] if pts else 0)
        if names:
            entry["flags"] = names
        if len({f for (_x, _y, f) in pts}) > 1:
            entry["point_flags"] = [f for (_x, _y, f) in pts]
        out.append(entry)
    return out


# ------------------------------------------------------------------------- build
def _lines(spec):
    return spec.get("lines", []) if isinstance(spec, dict) else (spec or [])


def _max_group(spec):
    return max((int(e["group"]) for e in _lines(spec)
               if isinstance(e, dict) and "group" in e), default=0)


def _flags(entry, at):
    v = 0
    for f in (entry.get("flags") or []):
        if isinstance(f, int):
            v |= f & 0xFF
        elif isinstance(f, str) and f.lower() in _FLAG_NAMES:
            v |= _FLAG_NAMES[f.lower()]
        elif isinstance(f, str) and f.lstrip("-").isdigit():
            v |= int(f) & 0xFF
        else:
            raise ValueError(f"{at}: unknown flag {f!r} (use drop_through, ledge, "
                             f"or a surface-index int)")
    return v


def _parse(spec, group_count, label):
    buckets: dict = {}
    for i, e in enumerate(_lines(spec)):
        at = f"{label}collision line {i}"
        if not isinstance(e, dict) or "group" not in e:
            raise ValueError(f"{at}: expected `- {{group: N, <type>: [x,y,...]}}`")
        gid = int(e["group"])
        if not 1 <= gid <= group_count:
            raise ValueError(f"{at}: group {gid} is out of range 1..{group_count}")
        tk = [t for t in _LINE_TYPES if t in e]
        if len(tk) != 1:
            raise ValueError(f"{at}: needs exactly one of {list(_LINE_TYPES)}")
        coords = e[tk[0]]
        if len(coords) < 4 or len(coords) % 2:
            raise ValueError(
                f"{at}: `{tk[0]}:` must be an even list of >=4 numbers (x,y,x,y,...)")
        pts = [(int(coords[k]), int(coords[k + 1]))
               for k in range(0, len(coords), 2)]
        for x, y in pts:
            if not (-32768 <= x <= 32767 and -32768 <= y <= 32767):
                raise ValueError(
                    f"{at}: point ({x}, {y}) is outside the s16 range "
                    f"[-32768, 32767] - collision coords are 16-bit. "
                    f"Clamp far-off wall/diagonal endpoints (the blast zone is "
                    f"only a few thousand units out anyway).")
        pf = e.get("point_flags")
        if pf is not None:
            if len(pf) != len(pts):
                raise ValueError(f"{at}: point_flags needs one value per point ({len(pts)})")
            flags = [int(v) & 0xFFFF for v in pf]
        else:
            flags = [_flags(e, at)] * len(pts)
        buckets.setdefault((gid, _LINE_TYPES.index(tk[0])), []).append(
            (pts, flags))
    return buckets


def _build(spec, group_count, label):
    buckets = _parse(spec, group_count, label)
    verts: list = []
    vflags: list = []
    vmap: dict = {}
    vid: list = []
    links: list = []
    line_info: list = []

    def vertex(x, y, flags):
        # Weld only vertices that ALSO agree on flags - the engine stores
        # drop-through / ledge / material per vertex, so merging a plain
        # corner with a drop-through one two lines over would silently make
        # the solid line passable. Same-flag coincident points still share an
        # index so lines can connect where you want them to.
        key = (x, y, flags)
        idx = vmap.get(key)
        if idx is None:
            idx = vmap[key] = len(verts)
            verts.append((x, y))
            vflags.append(flags)
        return idx

    for gid in range(1, group_count + 1):
        per_type = []
        for ti in range(4):
            first = len(links)
            for pts, flags in buckets.get((gid, ti), []):
                vfirst = len(vid)
                for (x, y), fl in zip(pts, flags):
                    vid.append(vertex(x, y, fl))
                links.append((vfirst, len(pts)))
            per_type.append((first, len(links) - first))
        line_info.append((gid, per_type))

    vd = b"".join(struct.pack(">hhH", x, y, f)
                  for (x, y), f in zip(verts, vflags))
    vi = struct.pack(f">{len(vid)}H", *vid) if vid else b""
    vl = b"".join(struct.pack(">HH", a, c) for a, c in links)
    li = b"".join(struct.pack(">H8H", yid, *[v for fc in pt for v in fc])
                  for yid, pt in line_info)
    return (vd, vi, vl, li), len(verts), len(links)


def _grow_groups(d, header_path, gd_off, cur, want, mapobjs_off, label):
    """Grow the collision-layer DObjDesc list (header.bin gr_desc[1] -> stage.bin)
    from `cur` to `want` group entries, in place. header.bin is not modified."""
    if want > _MAX_YAKUMONO_GROUPS:
        raise ValueError(f"{label}collision: {want} groups exceeds the engine's "
                         f"limit of {_MAX_YAKUMONO_GROUPS}")
    h = open(header_path, "rb").read()
    list_off = _u16(h, gd_off + 0x10 + 2) * 4          # gr_desc[1].dobjdesc
    o, n = list_off, 0
    while n <= 64 and struct.unpack_from(">i", d, o)[0] != 0x12:
        o, n = o + 0x2C, n + 1
    if struct.unpack_from(">i", d, o)[0] != 0x12:
        raise ValueError(f"{label}collision DObjDesc list @ 0x{list_off:X} has no "
                         f"id=0x12 terminator")
    if n - 1 != cur:
        logger.warning("%syakumono_count %d != DObjDesc group entries %d; "
                       "growing from the DObjDesc count", label, cur, n - 1)
        cur = n - 1
    blob = _DENTRY * (want - cur) + _DTERM
    if o + len(blob) > mapobjs_off:
        raise ValueError(
            f"{label}collision: not enough spare room in stage.bin to grow to "
            f"{want} groups (need 0x{o + len(blob):X}, map objects at "
            f"0x{mapobjs_off:X})")
    d[o:o + len(blob)] = blob


def apply(stage_path, spec, chain_head, *,
          header_path=None, groupdata_off=None, label=""):
    """Rewrite `stage_path` in place from the config.yaml `collision:` list.
    `chain_head` = offsets.stage[0]. Pass header_path + groupdata_off
    (offsets.header[1]) to allow growing the yakumono group count."""
    label = f"{label}: " if label else ""
    d = bytearray(open(stage_path, "rb").read())
    orig_len = len(d)

    geom = _find_geometry(d, _walk_chain(d, chain_head))
    if geom is None:
        raise ValueError(
            f"{label}could not locate MPGeometryData in stage.bin (every vanilla "
            f"stage's geometry auto-detects; a heavily custom one may not).")

    stage_count = _u16(d, geom)
    mapobjs_off = (_u32(d, geom + 0x18) & 0xFFFF) * 4
    want = max(stage_count, _max_group(spec))

    if want > _MAX_YAKUMONO_GROUPS:
        raise ValueError(
            f"{label}collision uses group {want}, but a stage supports at most "
            f"{_MAX_YAKUMONO_GROUPS} collision groups (GoldEditor and Remix's "
            f"stage loader both cap here; group {want} crashes on load at "
            f"0x8000F930). Fold the extra lines into an existing group.")

    if want > stage_count:
        if not (header_path and groupdata_off is not None):
            raise ValueError(
                f"{label}collision uses group {want} but the stage has "
                f"{stage_count}; growing needs header.bin (build via the appender, "
                f"or pass --header to stage_collision.py --build)")
        _grow_groups(d, header_path, groupdata_off, stage_count, want,
                     mapobjs_off, label)
        _w16(d, geom, want)
        logger.info("%scollision: grew yakumono groups %d -> %d",
                    label, stage_count, want)

    (arrays, nverts, nlines) = _build(spec, want, label)

    for field_off, blob in zip((0x04, 0x08, 0x0C, 0x10), arrays):
        while len(d) % 4:
            d.append(0)
        target = len(d)
        if target // 4 > 0xFFFE:
            raise ValueError(f"{label}stage.bin is too big to append collision data")
        d += blob
        _w16(d, geom + field_off + 2, target // 4)   # repoint, keep the chain link

    open(stage_path, "wb").write(d)
    return {"groups": want, "lines": nlines, "vertices": nverts,
            "bytes_added": len(d) - orig_len}


def _w_s16(d, o, v):
    struct.pack_into(">h", d, o, max(-32768, min(32767, int(v))))


_MAPOBJ_REBIRTH = 0x20   # "rebirth platform" map object kind


def set_rebirth(stage_path, chain_head, x, y, *, label=""):
    """Move the rebirth (revival) platform in stage.bin, in place. It is the
    map object of kind 0x20 in the geometry's mapobjs array (MPMapObjData =
    {u16 kind, s16 x, s16 y}); the engine finds it by kind, so there is one.
    header.bin is not touched."""
    label = f"{label}: " if label else ""
    d = bytearray(open(stage_path, "rb").read())
    geom = _find_geometry(d, _walk_chain(d, chain_head))
    if geom is None:
        raise ValueError(f"{label}could not locate MPGeometryData in stage.bin")
    mo_cnt = _u16(d, geom + 0x14)
    p_mo = (_u32(d, geom + 0x18) & 0xFFFF) * 4
    for i in range(mo_cnt):
        if _u16(d, p_mo + i * 6) == _MAPOBJ_REBIRTH:
            _w_s16(d, p_mo + i * 6 + 2, x)
            _w_s16(d, p_mo + i * 6 + 4, y)
            open(stage_path, "wb").write(d)
            return (int(x), int(y))
    raise ValueError(f"{label}stage has no rebirth-platform map object (kind 0x20) "
                     f"to move - add one in GoldEditor first")
