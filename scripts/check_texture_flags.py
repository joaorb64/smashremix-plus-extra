#!/usr/bin/env python3
"""
check_texture_flags.py - checks a GE model .bin for texture properties
GE's own BMP export loses: palette-index-0 transparency, UV wrap/clamp mode.

  python3 scripts/check_texture_flags.py MODEL.bin [--table 0x40C]

For each texture:
  - Palette alpha: for CI textures, checks if palette index 0 has alpha=0
    (the usual "transparent hole" convention a flat BMP can't carry).
  - UV wrap mode: reads the tile-0 G_SETTILE cms/cmt bits, reports clamp,
    wrap, mirror or mirror+clamp per axis - GE's material doesn't show this.

Read-only report - doesn't touch the .bin or the FBX.
"""
import argparse
import bisect
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from smashremix_extra.ge_bin import (  # noqa: E402
    FMT_NAME, SIZ_BPP, u16, u32, find_chain_head, find_textures, walk_chain)

WRAP_NAMES = {(0, 0): "wrap", (1, 0): "mirror",
              (0, 1): "clamp", (1, 1): "mirror+clamp"}


def settile_flags(d, cmd_off, span=40):
    """Returns cms, cmt from the tile-0 G_SETTILE nearest cmd_off, or None."""
    cands = []
    for k in range(-span, span + 1):
        o = cmd_off + k * 8
        if not (0 <= o + 8 <= len(d)):
            continue
        op = d[o]
        if op in (0xDF, 0xB8) and k != 0:
            if k < 0:
                cands = [c for c in cands if c[0] > k]
                continue
            break
        if op == 0xF5:
            w1 = u32(d, o + 4)
            if (w1 >> 24) & 7 == 0:
                mirror_t, clamp_t = (w1 >> 19) & 1, (w1 >> 18) & 1
                mirror_s, clamp_s = (w1 >> 9) & 1, (w1 >> 8) & 1
                cands.append((k, (mirror_s, clamp_s), (mirror_t, clamp_t)))
    if not cands:
        return None
    before = [c for c in cands if c[0] < 0]
    pick = max(before) if before else min(cands)
    return pick[1], pick[2]


G_LIGHTING = 0x00020000


def find_vtx_commands(d, nodes):
    """Returns (cmd_off, numv) for every real G_VTX command, found via the
    relocation chain so it doesn't false-positive on random bytes."""
    out = []
    for n in nodes:
        if n.field >= 4 and d[n.field - 4] == 0x01:
            cmd_off = n.field - 4
            w0 = u32(d, cmd_off)
            numv = (w0 >> 12) & 0xFFF
            out.append((cmd_off, numv))
    return out


G_MW_LIGHTCOL = 0x0A


def light_colors_before(d, cmd_off, span=200):
    """Returns (offset, (r,g,b)) for light colors in effect at cmd_off -
    walks backward to find each distinct G_MOVEWORD LIGHTCOL offset, closest
    one wins. Offset 0x00 is usually the diffuse color, 0x18 the shadow/
    ambient one, but that can shift, so it's returned as-is not guessed."""
    found = {}
    for k in range(-1, -span - 1, -1):
        o = cmd_off + k * 8
        if not (0 <= o + 8 <= len(d)):
            break
        op = d[o]
        if op in (0xDF, 0xB8):
            break
        if op == 0xDB and d[o + 1] == G_MW_LIGHTCOL:
            offset = u16(d, o + 2)
            if offset in found:
                continue  # a nearer command already set this field
            value = u32(d, o + 4)
            rgb = (value >> 24) & 0xFF, (value >> 16) & 0xFF, (value >> 8) & 0xFF
            found[offset] = rgb
    return sorted(found.items())


G_CULL_FRONT = 0x00001000
G_CULL_BACK = 0x00002000
G_CULL_BOTH = G_CULL_FRONT | G_CULL_BACK


def geometry_bits_before(d, cmd_off, bits, span=200):
    """Returns {bit: True/False} walking backward from cmd_off, checking
    G_GEOMETRYMODE commands. A bit missing means it wasn't touched - state
    unknown."""
    found = {}
    remaining = set(bits)
    for k in range(-1, -span - 1, -1):
        if not remaining:
            break
        o = cmd_off + k * 8
        if not (0 <= o + 8 <= len(d)):
            break
        op = d[o]
        if op in (0xDF, 0xB8):
            break  # crossed a display-list boundary - stop, state unknown
        if op == 0xD9:
            w0, w1 = u32(d, o), u32(d, o + 4)
            clearmask = (~w0) & 0xFFFFFF
            for bit in list(remaining):
                if w1 & bit:
                    found[bit] = True
                    remaining.discard(bit)
                elif clearmask & bit:
                    found[bit] = False
                    remaining.discard(bit)
    return found


def lighting_state_before(d, cmd_off, span=200):
    """True (lit via normals), False (off - vertex colors used directly),
    or None if no nearby G_GEOMETRYMODE command touches G_LIGHTING."""
    return geometry_bits_before(d, cmd_off, [G_LIGHTING], span).get(G_LIGHTING)


def cull_both_before(d, cmd_off, span=200):
    """True = both faces render (GE's cullBoth flag), False = normal
    culling, None = unknown."""
    bits = geometry_bits_before(d, cmd_off, [G_CULL_FRONT, G_CULL_BACK], span)
    front, back = bits.get(G_CULL_FRONT), bits.get(G_CULL_BACK)
    if front is None and back is None:
        return None
    return not (front or back)


def find_expression_groups(texs, proximity=0x2000):
    """Finds candidate "Special Image" groups (e.g. facial expressions):
    textures with the same format/size/palette that sit close together in
    the file. Just a structural guess, needs visual confirmation."""
    non_tlut = [t for t in texs if not t["is_tlut"]]
    by_key = {}
    for t in non_tlut:
        tlut_off = t["tlut"]["off"] if t.get("tlut") else None
        key = (t["fmt"], t["siz"], t["w"], t["h"], tlut_off)
        by_key.setdefault(key, []).append(t)

    groups = []
    for key, members in by_key.items():
        members = sorted({m["off"]: m for m in members}.values(),
                         key=lambda m: m["off"])
        if len(members) < 2:
            continue
        cluster = [members[0]]
        for m in members[1:]:
            if m["off"] - cluster[-1]["off"] <= proximity:
                cluster.append(m)
            else:
                if len(cluster) >= 2:
                    groups.append(cluster)
                cluster = [m]
        if len(cluster) >= 2:
            groups.append(cluster)
    return groups


ROOM_CONNECT_RE = re.compile(
    r'Connect: "OO", "Material::([0-9A-Fa-f]{8})[^"]*", "Model::(Room[0-9A-Fa-f]{2})"')


def parse_material_room_map(fbx_text):
    """Returns {texture_addr_hex: {RoomXX, ...}} from an exported FBX's own
    Material->Model connect lines. A texture can map to more than one room
    (shared between joints)."""
    room_map = {}
    for addr, room in ROOM_CONNECT_RE.findall(fbx_text):
        room_map.setdefault(addr.upper(), set()).add(room)
    return room_map


def compute_room_light_tags(d, nodes, texs, room_map):
    """Returns ({RoomXX: (light_rgb, shadow_rgb)}, {RoomXX: {setup, ...}}).
    For each lit Vtx block, finds its bound texture and, via room_map,
    which room(s) it belongs to. A room only gets a single tag if every
    Vtx block agrees on one setup - otherwise it goes in the mixed dict
    (the room has more than one light/shadow setup in it)."""
    non_tlut = sorted((t for t in texs if not t["is_tlut"]), key=lambda t: t["cmd"])
    tex_cmds = [t["cmd"] for t in non_tlut]

    def nearest_tex_before(cmd_off):
        i = bisect.bisect_right(tex_cmds, cmd_off) - 1
        return non_tlut[i] if i >= 0 else None

    vtx_cmds = find_vtx_commands(d, nodes)
    room_setups = {}
    for cmd_off, _numv in vtx_cmds:
        colors = light_colors_before(d, cmd_off)
        if not colors or len(colors) < 2:
            continue
        t = nearest_tex_before(cmd_off)
        if t is None:
            continue
        for room in room_map.get(f"{t['off']:08X}", set()):
            room_setups.setdefault(room, set()).add(tuple(colors))

    tags, mixed = {}, {}
    for room, setups in room_setups.items():
        if len(setups) == 1:
            colors = next(iter(setups))
            tags[room] = (colors[0][1], colors[-1][1])
        else:
            mixed[room] = setups
    return tags, mixed


def compute_texture_setup_correlation(d, nodes, texs):
    """Returns {texture_addr_hex: (light_rgb, shadow_rgb)} - the dominant
    light setup each texture is drawn under, globally (not per room).
    A texture GE only reaches through special-part indirection won't show
    up here, callers need a fallback for that."""
    non_tlut = sorted((t for t in texs if not t["is_tlut"]), key=lambda t: t["cmd"])
    tex_cmds = [t["cmd"] for t in non_tlut]

    def nearest_tex_before(cmd_off):
        i = bisect.bisect_right(tex_cmds, cmd_off) - 1
        return non_tlut[i] if i >= 0 else None

    vtx_cmds = find_vtx_commands(d, nodes)
    counts = {}
    for cmd_off, _numv in vtx_cmds:
        colors = light_colors_before(d, cmd_off)
        if not colors or len(colors) < 2:
            continue
        t = nearest_tex_before(cmd_off)
        if t is None:
            continue
        setup = (colors[0][1], colors[-1][1])
        addr = f"{t['off']:08X}"
        counts.setdefault(addr, {})
        counts[addr][setup] = counts[addr].get(setup, 0) + 1

    return {addr: max(setups.items(), key=lambda kv: kv[1])[0]
            for addr, setups in counts.items()}


PART_HEADER_RE = re.compile(r'^Part ([0-9A-Fa-f]{2}) Offset ([0-9A-Fa-f]+)$', re.M)
# there's also a separate "Special Data" section (Special Part NN -> Hi/Lo
# Res Special Part Offset ...) for swappable special-part geometry. A
# joint's Part NN entry can be empty while Special Part NN still has real
# content (e.g. special parts with no default geometry). Same NN as RoomXX.
SPECIAL_PART_HEADER_RE = re.compile(r'^Special Part ([0-9A-Fa-f]{2})$', re.M)
# move word (light) + the two draw call kinds, in file order. a setup
# overwritten before any draw uses it doesn't count.
DL_EVENT_RE = re.compile(
    r'Move Word Index 0A Offset (0000|0018) Value ([0-9A-Fa-f]{8})'
    r'|(Setting Vertice)|(Tri[12])')


def _scan_setups(block):
    """Returns [(light_rgb, shadow_rgb), ...] - every light setup actually
    used by a draw call in `block`, in first-seen order. A setup that gets
    overwritten before anything draws with it is skipped."""
    setups, cur_light, cur_shadow, used = [], None, None, False
    for ev in DL_EVENT_RE.finditer(block):
        off, val, is_vtx, is_tri = ev.groups()
        if off is not None:
            if off == "0000":
                if cur_shadow is not None and used:
                    setups.append((cur_light, cur_shadow))
                cur_light = (int(val[0:2], 16), int(val[2:4], 16), int(val[4:6], 16))
                cur_shadow, used = None, False
            else:  # 0018
                cur_shadow = (int(val[0:2], 16), int(val[2:4], 16), int(val[4:6], 16))
                used = False
        elif cur_shadow is not None:
            used = True
    if cur_shadow is not None and used:
        setups.append((cur_light, cur_shadow))
    return setups


def parse_displaylist_dump(text):
    """Parses GE's "<Name>DisplayLists.txt" debug dump (ground truth, not
    inference - GE already disassembled every joint's display list).
    Reads both the per-Room "Part XX" section and the "Special Part XX"
    section, merged together.

    Returns {RoomXX: [(light_rgb, shadow_rgb), ...]} - usually one setup
    per room; more than one means the room has different light/shadow
    colors in different parts of it."""
    rooms = {}

    headers = list(PART_HEADER_RE.finditer(text))
    for i, m in enumerate(headers):
        if m.group(2) == "0" * len(m.group(2)):
            continue
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        room = f"Room{m.group(1).upper()}"
        seen = rooms.setdefault(room, [])
        for s in _scan_setups(text[m.end():end]):
            if s not in seen:
                seen.append(s)

    sp_headers = list(SPECIAL_PART_HEADER_RE.finditer(text))
    for i, m in enumerate(sp_headers):
        end = sp_headers[i + 1].start() if i + 1 < len(sp_headers) else len(text)
        block = text[m.end():end]
        if "Special Part Offset" not in block:
            continue
        room = f"Room{m.group(1).upper()}"
        seen = rooms.setdefault(room, [])
        for s in _scan_setups(block):
            if s not in seen:
                seen.append(s)

    return rooms


def room_light_tags_from_dump(text):
    """Same shape as compute_room_light_tags(), but from
    parse_displaylist_dump() instead."""
    rooms = parse_displaylist_dump(text)
    tags, mixed = {}, {}
    for room, setups in rooms.items():
        if len(setups) == 1:
            tags[room] = setups[0]
        elif setups:
            mixed[room] = setups
    return tags, mixed


def palette_alpha0(d, tlut):
    """True if tlut's index-0 RGBA5551 entry has alpha bit 0."""
    if tlut is None or tlut.get("off") is None:
        return None
    off = tlut["off"]
    if off + 2 > len(d):
        return None
    entry = u16(d, off)
    return (entry & 1) == 0


def compute_material_tags(d, texs):
    """Returns {texture_off: (kind, dims, [tag, ...])} for textures needing
    a tag (Transparent/ClampS/ClampT/MirrorS/MirrorT), plus flagged_alpha/
    wrap_entries for reporting, and cull_state: {texture_off:
    True/False/None}. CullBoth is left out of tags on purpose - None means
    "don't know", not "off", so callers must handle it instead of
    overwriting a real CullBoth with nothing."""
    flagged_alpha = []
    wrap_entries = []  # (off, kind, dims, s_name, t_name, mode_pair)
    material_tags = {}  # off -> (kind, dims, [tag, ...])
    cull_state = {}  # off -> True/False/None
    for t in texs:
        if t["is_tlut"]:
            continue
        kind = f"{FMT_NAME.get(t['fmt'], '?')}{SIZ_BPP.get(t['siz'], '?')}b"
        dims = f"{t['w']}x{t['h']}" if t['w'] else "-"
        tags = []

        if t["fmt"] == 2:  # CI
            a0 = palette_alpha0(d, t.get("tlut"))
            if a0:
                flagged_alpha.append((t["off"], kind, dims))
                tags.append("Transparent")

        flags = settile_flags(d, t["cmd"])
        if flags:
            (ms, cs), (mt, ct) = flags
            s_name = WRAP_NAMES[(ms, cs)]
            t_name = WRAP_NAMES[(mt, ct)]
            wrap_entries.append(
                (t["off"], kind, dims, s_name, t_name, (s_name, t_name)))
            # wrap is the default, no tag needed. clamp/mirror get their own
            # tag per axis. mirror+clamp together has no known tag, skipped.
            axis_tag = {"clamp": "Clamp", "mirror": "Mirror"}
            if s_name != "wrap":
                tags.append(f"{axis_tag[s_name]}S" if s_name in axis_tag
                            else f"S={s_name}(?)")
            if t_name != "wrap":
                tags.append(f"{axis_tag[t_name]}T" if t_name in axis_tag
                            else f"T={t_name}(?)")

        cull_state[t["off"]] = cull_both_before(d, t["cmd"])

        if tags:
            material_tags[t["off"]] = (kind, dims, tags)

    return material_tags, flagged_alpha, wrap_entries, cull_state


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model", help="GE model .bin file")
    ap.add_argument("--table", type=lambda x: int(x, 0), default=None,
                    help="internal file table offset (auto-detected if omitted)")
    ap.add_argument("--displaylist", help="GE's \"<Name>DisplayLists.txt\" debug "
                    "dump for this character (Export Model Displaylists in GE) - "
                    "ground truth for LightColor/ShadowColor per RoomXX, preferred "
                    "over --fbx when both are given")
    ap.add_argument("--fbx", help="a previously-exported reference FBX for this "
                    "character - used to resolve LightColor/ShadowColor tags to "
                    "real RoomXX names via its own Material->Model Connect lines "
                    "when --displaylist isn't available (omit both to fall back "
                    "to the 'Room??' placeholder)")
    a = ap.parse_args()

    d = Path(a.model).read_bytes()
    head = a.table if a.table is not None else find_chain_head(d)
    if head is None:
        raise SystemExit("couldn't find the self-relocation chain head - "
                         "pass --table explicitly")
    nodes = walk_chain(d, head)
    texs = find_textures(d, nodes=nodes)

    material_tags, flagged_alpha, wrap_entries, cull_state = compute_material_tags(d, texs)

    print(f"{len(texs) - sum(1 for t in texs if t['is_tlut'])} texture(s) scanned "
          f"in {a.model}\n")

    print(f"Palette index 0 has alpha=0 ({len(flagged_alpha)}):")
    if not flagged_alpha:
        print("  (none)")
    for off, kind, dims in flagged_alpha:
        print(f"  0x{off:06X}  {kind:<8} {dims}")

    # no universal "expected" wrap mode, so use whichever is most common
    # here as this file's own baseline and flag the rest as outliers
    mode_counts = {}
    for *_, mode in wrap_entries:
        mode_counts[mode] = mode_counts.get(mode, 0) + 1
    print(f"\nUV wrap mode distribution ({len(wrap_entries)} textures):")
    for mode, count in sorted(mode_counts.items(), key=lambda x: -x[1]):
        print(f"  S={mode[0]:<12} T={mode[1]:<12} x{count}")

    baseline = max(mode_counts, key=mode_counts.get) if mode_counts else None
    outliers = [e for e in wrap_entries if e[5] != baseline]
    print(f"\nOutliers vs. this file's own baseline "
          f"(S={baseline[0]}, T={baseline[1]}) ({len(outliers)}):")
    if not outliers:
        print("  (none)")
    for off, kind, dims, s_name, t_name, _mode in outliers:
        print(f"  0x{off:06X}  {kind:<8} {dims:<8} S={s_name} T={t_name}")

    confirmed_cull = {off for off, state in cull_state.items() if state is True}
    unknown_cull = {off for off, state in cull_state.items() if state is None}
    all_offs = sorted(set(material_tags) | confirmed_cull)
    print(f"\nSuggested material name tags ({len(all_offs)} "
          f"textures with at least one non-default flag):")
    if not all_offs:
        print("  (none - nothing detected worth tagging)")
    for off in all_offs:
        kind, dims, tags = material_tags.get(off, (None, None, []))
        if kind is None:
            t0 = next(t for t in texs if not t["is_tlut"] and t["off"] == off)
            kind = f"{FMT_NAME.get(t0['fmt'], '?')}{SIZ_BPP.get(t0['siz'], '?')}b"
            dims = f"{t0['w']}x{t0['h']}" if t0['w'] else "-"
        full_tags = (["CullBoth"] if off in confirmed_cull else []) + tags
        print(f"  0x{off:06X}  {kind:<8} {dims:<8} -> {''.join(full_tags)}")
    if unknown_cull:
        print(f"\n  {len(unknown_cull)} texture(s) have no determinable cull "
              "state (inherited from elsewhere) - CullBoth left as-is on these:")
        for off in sorted(unknown_cull):
            print(f"    0x{off:06X}")

    # a G_VTX block with lighting off uses vertex colors directly, GE's FBX
    # export can't tell that apart from a lit mesh
    vtx_cmds = find_vtx_commands(d, nodes)
    unlit = []
    unknown = 0
    for cmd_off, numv in vtx_cmds:
        lit = lighting_state_before(d, cmd_off)
        if lit is None:
            unknown += 1
        elif lit is False:
            unlit.append((cmd_off, numv))
    print(f"\nVtx blocks rendered with lighting OFF (vertex colors), "
          f"of {len(vtx_cmds)} G_VTX command(s) found "
          f"({unknown} with no nearby G_GEOMETRYMODE to tell) "
          f"({len(unlit)}):")
    if not unlit:
        print("  (none)")
    for cmd_off, numv in unlit:
        print(f"  0x{cmd_off:06X}  {numv} vert(s)")

    # dedupe light setups across VTX blocks, print each once
    setups = {}
    for cmd_off, numv in vtx_cmds:
        colors = light_colors_before(d, cmd_off)
        if colors:
            setups.setdefault(tuple(colors), []).append(cmd_off)
    print(f"\nLight colors in effect at lit Vtx blocks "
          f"({len(setups)} distinct setup(s)):")
    if not setups:
        print("  (none found)")

    room_tags, room_mixed = {}, {}
    room_source = None
    if a.displaylist:
        room_tags, room_mixed = room_light_tags_from_dump(Path(a.displaylist).read_text(
            encoding="utf-8", errors="surrogateescape"))
        room_source = a.displaylist
    elif a.fbx:
        room_map = parse_material_room_map(Path(a.fbx).read_text(
            encoding="utf-8", errors="surrogateescape"))
        room_tags, room_mixed = compute_room_light_tags(d, nodes, texs, room_map)
        room_source = a.fbx
    if room_source:
        rooms_by_setup = {}
        for room, colors in room_tags.items():
            rooms_by_setup.setdefault(colors, []).append(room)

    for colors, cmd_offs in setups.items():
        where = ", ".join(f"0x{o:06X}" for o in cmd_offs[:6])
        if len(cmd_offs) > 6:
            where += f", +{len(cmd_offs) - 6} more"
        print(f"  used at: {where}")
        for offset, (r, g, b) in colors:
            print(f"    field offset 0x{offset:02X}: RGB({r},{g},{b})")
        # lowest offset = light color, highest = shadow/ambient color
        if len(colors) >= 2:
            # tag format: RRGGBB + hardcoded FF alpha, no separator
            light_rgb, shadow_rgb = colors[0][1], colors[-1][1]
            suffix = (f"LightColor{light_rgb[0]:02X}{light_rgb[1]:02X}{light_rgb[2]:02X}FF"
                     f"ShadowColor{shadow_rgb[0]:02X}{shadow_rgb[1]:02X}{shadow_rgb[2]:02X}FF")
            if room_source:
                rooms = rooms_by_setup.get((light_rgb, shadow_rgb), [])
                if rooms:
                    for room in sorted(rooms):
                        print(f"    -> {room}{suffix}")
                else:
                    print(f"    -> Room??{suffix}  (no room in {room_source} "
                         "resolved to this exact setup)")
            else:
                print(f"    -> Room??{suffix}")

    if room_source:
        if room_mixed:
            print(f"\nRooms with more than one light setup observed "
                  f"({len(room_mixed)}) - not auto-tagged, needs a closer look:")
            for room, setups_seen in sorted(room_mixed.items()):
                print(f"  {room}: {len(setups_seen)} different setups seen")
    else:
        print("\nNote: LightColor/ShadowColor tags above use 'Room??' as a "
              "placeholder - pass --displaylist <Name>DisplayLists.txt (GE's "
              "own \"Export Model Displaylists\" dump, preferred - ground "
              "truth, not inference) or --fbx <reference.fbx> to resolve "
              "real RoomXX names.")

    # written to a separate candidates file, doesn't touch specialtextures.txt
    groups = find_expression_groups(texs)
    print(f"\nCandidate Special Image groups ({len(groups)}) - "
          "needs your visual confirmation, not auto-applied:")
    lines = []
    if not groups:
        print("  (none found)")
    for members in groups:
        kind = f"{FMT_NAME.get(members[0]['fmt'], '?')}{SIZ_BPP.get(members[0]['siz'], '?')}b"
        dims = f"{members[0]['w']}x{members[0]['h']}"
        addrs = [f"0x{m['off']:06X}" for m in members]
        print(f"  {kind} {dims}: {', '.join(addrs)}")
        base = f"{members[0]['off']:08X}.bmp"
        lines.append(f"Special Image: {base}")
        for m in members[1:]:
            lines.append(f"Alt: {m['off']:08X}.bmp")
        lines.append("")

    if lines:
        out_path = Path(a.model).with_name(
            Path(a.model).stem + "_specialtextures_candidates.txt")
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\nWrote candidate blocks to {out_path}")


if __name__ == "__main__":
    main()
