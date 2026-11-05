#!/usr/bin/env python3
"""
ge_bin.py - shared primitives for GoldEditor / SSB64 self-relocating .bin models.

A GE model .bin is self-relocating: every pointer inside it is a 4-byte field
holding ``[u16 next_word][u16 data_word]``.  All of those fields are threaded
into ONE singly-linked list whose head is the file's "internal table offset"
(the value in config.yaml ``files:`` column 1, a.k.a. the appender's
``internal_file_table_offset``).  For a field at offset F:

    next  = u16(F)   * 4     # file offset of the next pointer field
    data  = u16(F+2) * 4     # file offset this field points at
    the chain ends when next is 0xFFFF*4 (0x3FFFC), 0, or out of range.

At load time the game walks the chain and adds the file's RAM base to every
``data`` slot, turning the file into live pointers.

Layout rule GE always follows: data is emitted in ascending offset order, and
every struct / display list / texture / TLUT / vertex block that anything points
at begins exactly at a fixup *target*.  So the sorted set of targets partitions
the file into regions and "region end" == "next target".  Every tool here is
built on that.

Nothing in this module writes files; callers do that.
"""
from __future__ import annotations

import struct
from collections import namedtuple

CHAIN_END = 0x3FFFC  # 0xFFFF * 4

Node = namedtuple("Node", "field next target")   # next is None at the chain end
Obj = namedtuple("Obj", "start end ids")
Footer = namedtuple("Footer", "dobjdesc pmobjsubs dd_node pm_node nodes")

SIZ_BPP = {0: 4, 1: 8, 2: 16, 3: 32}
FMT_NAME = {0: "RGBA", 1: "YUV", 2: "CI", 3: "IA", 4: "I"}


# --------------------------------------------------------------------------- io
def u16(b, o): return struct.unpack_from(">H", b, o)[0]
def u32(b, o): return struct.unpack_from(">I", b, o)[0]
def w16(b, o, v): struct.pack_into(">H", b, o, v & 0xFFFF)
def w32(b, o, v): struct.pack_into(">I", b, o, v & 0xFFFFFFFF)


# ------------------------------------------------------------------- fixup chain
def walk_chain(d, head):
    """Follow the fixup chain from ``head``.  Returns [Node(field, next, target)];
    the last node's ``next`` is None.  Stops on a cycle or an out-of-range hop."""
    out, off, seen = [], head, set()
    while 0 <= off < len(d) - 3 and off not in seen:
        seen.add(off)
        nxt, tgt = u16(d, off) * 4, u16(d, off + 2) * 4
        end = nxt in (CHAIN_END, 0) or nxt >= len(d)
        out.append(Node(off, None if end else nxt, tgt))
        if end:
            break
        off = nxt
    return out


def find_chain_head(d, min_nodes=8):
    """Brute-force the internal table offset: try every 4-aligned start, keep the
    one that walks the most in-range, properly-terminated nodes (ties -> lowest
    offset).  Returns None if nothing plausible.  Use when GE / config.yaml did
    not tell you the offset, or to sanity-check one that did."""
    best = None  # (node_count, -head)
    for head in range(0, len(d) - 4, 4):
        off, seen, cnt, ok = head, set(), 0, True
        while 0 <= off <= len(d) - 4 and off not in seen:
            seen.add(off)
            nxt, tgt = u16(d, off) * 4, u16(d, off + 2) * 4
            if tgt > len(d) or nxt % 4:
                ok = False
                break
            cnt += 1
            if nxt in (CHAIN_END, 0) or nxt >= len(d):
                break
            off = nxt
        if ok and cnt >= min_nodes:
            key = (cnt, -head)
            if best is None or key > best[0]:
                best = (key, head)
    return best[1] if best else None


# ---------------------------------------------------------------------- regions
def region_bounds(d, nodes, extra=()):
    """Sorted region boundaries: 0, len(d), every fixup target, plus ``extra``."""
    return sorted(set([0, len(d)] + [n.target for n in nodes] + list(extra)))


def region_of(bounds, x):
    """(start, end) of the region containing offset x."""
    lo, hi, best = 0, len(bounds) - 1, bounds[0]
    while lo <= hi:
        mid = (lo + hi) // 2
        if bounds[mid] <= x:
            best, lo = bounds[mid], mid + 1
        else:
            hi = mid - 1
    i = bounds.index(best)
    return best, (bounds[i + 1] if i + 1 < len(bounds) else best)


def reachable_regions(nodes, bounds, roots):
    """Set of region-start offsets reachable from any offset in ``roots`` by
    following fixup targets region-to-region."""
    keep, work = set(), [region_of(bounds, r)[0] for r in roots]
    while work:
        s = work.pop()
        if s in keep:
            continue
        keep.add(s)
        _, e = region_of(bounds, s)
        for nd in nodes:
            if s <= nd.field < e:
                work.append(region_of(bounds, nd.target)[0])
    return keep


# ---------------------------------------------------------------------- objects
def find_objects(d):
    """Locate DObjDesc lists ("objects" / "models" in GE terms).  A list starts
    with the 16-byte preamble ``[?][ptr][00000004][00000000]`` then repeats
    0x2C-byte entries until an entry id of 0x12 (the list terminator).  Returns
    [Obj(start, end, entry_ids)] in file order."""
    objs = []
    n = len(d)
    for h in range(0, n - 16, 4):
        if u32(d, h + 8) != 4 or u32(d, h + 12) != 0:
            continue
        e, ids = h + 0x10, []
        while e + 4 <= n and u32(d, e) != 0x12 and len(ids) < 32:
            ids.append(u32(d, e))
            e += 0x2C
        if e + 4 <= n and u32(d, e) == 0x12:
            objs.append(Obj(h, e + 4, ids))
    return objs


# --------------------------------------------------------------------- textures
def find_textures(d, head=None, nodes=None):
    """Every image / TLUT the display lists reference, with format+dims recovered
    from the neighbouring SETTILE / SETTILESIZE / LOADTLUT / LOADBLOCK.  Each
    record: dict(cmd, off, fmt, siz, w, h, is_tlut, tlut_count, region_bytes,
    tlut).  Dependency-free (no PIL/numpy); decoding lives in ge_texture.py."""
    if nodes is None:
        if head is None:
            head = find_chain_head(d)
        nodes = walk_chain(d, head)
    targets = sorted(set(n.target for n in nodes) | {len(d)})

    def next_target(x):
        for t in targets:
            if t > x:
                return t
        return len(d)

    settimgs = [(n.field - 4, n.target)
                for n in nodes if n.field >= 4 and d[n.field - 4] == 0xFD]

    def scan(cmd_off, span=40):
        out = []
        for k in range(-span, span + 1):
            o = cmd_off + k * 8
            if not (0 <= o + 8 <= len(d)):
                continue
            op = d[o]
            if op in (0xDF, 0xB8) and k != 0:
                if k < 0:
                    out = [x for x in out if x[4] > k]
                    continue
                break
            out.append((o, op, u32(d, o), u32(d, o + 4), k))
        return out

    texs = []
    for cmd_off, img_off in settimgs:
        fmt = siz = w = h = None
        is_tlut = False
        tlut_count = None
        ctx = scan(cmd_off)
        b1 = d[cmd_off + 1]
        timg_siz, timg_fmt = (b1 >> 3) & 3, (b1 >> 5) & 7
        for _o, op, _w0, w1, k in ctx:
            if op == 0xF0 and 0 < k <= 3:
                is_tlut = True
                tlut_count = ((w1 >> 14) & 0x3FF) + 1
        if not is_tlut:
            cands = [(k, (w0 >> 21) & 7, (w0 >> 19) & 3)
                     for _o, op, w0, w1, k in ctx
                     if op == 0xF5 and ((w1 >> 24) & 7) == 0]
            before = [c for c in cands if c[0] < 0]
            pick = max(before) if before else (min(cands) if cands else None)
            if pick:
                fmt, siz = pick[1], pick[2]
            if fmt is None:
                fmt, siz = timg_fmt, timg_siz
            bpp = SIZ_BPP[siz]
            lb = next((w1 for _o, op, _w0, w1, k in ctx
                       if op == 0xF3 and 0 < k <= 3), None)
            if lb is not None:
                dxt = lb & 0xFFF
                if dxt:
                    w = (round(2048 / dxt) * 8) * 8 // bpp
            f2 = sorted((abs(k), w1) for _o, op, _w0, w1, k in ctx if op == 0xF2)
            if f2:
                w1s = f2[0][1]
                w_st = (((w1s >> 12) & 0xFFF) >> 2) + 1
                h_st = ((w1s & 0xFFF) >> 2) + 1
                span = next_target(img_off) - img_off
                if span - 8 <= w_st * h_st * bpp // 8 <= span:
                    w, h = w_st, h_st
            if w and not h:
                total = ((next_target(img_off) - img_off) // 8) * 8
                for tot in (next_target(img_off) - img_off, total,
                            total - 8 if total >= 8 else total):
                    if tot > 0 and (tot * 8) % (w * bpp) == 0:
                        h = tot * 8 // (w * bpp)
                        break
        if fmt is None:
            fmt, siz = timg_fmt, timg_siz
        texs.append(dict(cmd=cmd_off, off=img_off, fmt=fmt, siz=siz, w=w, h=h,
                         is_tlut=is_tlut, tlut_count=tlut_count,
                         region_bytes=next_target(img_off) - img_off))

    tluts = [t for t in texs if t["is_tlut"]]
    for t in texs:
        if t["is_tlut"]:
            continue
        cand = [x for x in tluts if x["cmd"] < t["cmd"]]
        t["tlut"] = cand[-1] if cand else (tluts[0] if tluts else None)
    return texs


# ----------------------------------------------------------------------- footer
def footer_roots(footer_bytes):
    """Parse a ``<name>_hitbox.bin`` ITAttributes footer.  Its own tiny fixup
    chain starts at 0x40: node 0 -> DObjDesc list in the model, node 1 (if
    present) -> p_mobjsubs.  Returns Footer(dobjdesc, pmobjsubs, dd_node,
    pm_node, nodes)."""
    ch = walk_chain(footer_bytes, 0x40)
    if not ch:
        raise ValueError("footer has no fixup chain at 0x40")
    dd_node = ch[0].field
    dd = u16(footer_bytes, dd_node + 2) * 4
    pm_node = ch[1].field if len(ch) > 1 else None
    pm = (u16(footer_bytes, pm_node + 2) * 4) if pm_node is not None else None
    return Footer(dd, pm, dd_node, pm_node, ch)


def resolve_trim_roots(d, trim, footer_bytes=None):
    """gc() roots from a trim: value. True -> footer DObjDesc (+ p_mobjsubs);
    {objects: [n]} -> find_objects() indices; [off, ...] -> raw offsets."""
    if trim is True:
        if footer_bytes is None:
            raise ValueError("trim: true needs a footer: to root from")
        fr = footer_roots(footer_bytes)
        roots = [fr.dobjdesc]
        if fr.pmobjsubs is not None:
            roots.append(fr.pmobjsubs)
        return roots
    if isinstance(trim, dict) and "objects" in trim:
        objs = find_objects(d)
        idxs = trim["objects"]
        idxs = idxs if isinstance(idxs, list) else [idxs]
        try:
            return [objs[i].start + 0x10 for i in idxs]
        except IndexError:
            raise ValueError(
                f"trim: objects: index out of range - file has {len(objs)} "
                f"object(s) (indices 0..{len(objs) - 1}, or negative)")
    return [x if isinstance(x, int) else int(x, 16) for x in trim]


# ------------------------------------------------------------- garbage collector
GcResult = namedtuple(
    "GcResult",
    "data new_head kept dropped remap new_len old_len report")


def gc(d, roots, drop=(), head=None):
    """Mark-and-sweep a GE .bin down to the regions reachable from ``roots``
    (a list of file offsets - typically a footer's DObjDesc + p_mobjsubs, or a
    single object's start), recompact, and rebuild the fixup chain.

    ``drop`` = extra [(start, end)] byte ranges to force out (dead DL tails the
    region sweep keeps because they share a region with a live ENDDL).

    Returns GcResult.  ``remap`` is a dict {old_offset: new_offset} covering
    every surviving byte - use ``remap.get(x)`` to translate any anchor
    (texture offset, sprite-array offset, ...) from the old file to the new one.
    Does not touch the filesystem.
    """
    d = bytes(d)
    n = len(d)
    if head is None:
        head = find_chain_head(d)
        if head is None:
            raise ValueError("could not auto-detect chain head; pass head=")
    nodes = walk_chain(d, head)
    bounds = region_bounds(d, nodes, extra=roots)

    keep = reachable_regions(nodes, bounds, roots)
    ranges = sorted((s, region_of(bounds, s)[1]) for s in keep)
    merged = []
    for s, e in ranges:
        if merged and merged[-1][1] == s:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))

    for ds, de in drop:
        cut = []
        for s, e in merged:
            if e <= ds or s >= de:
                cut.append((s, e))
                continue
            if s < ds:
                cut.append((s, ds))
            if e > de:
                cut.append((de, e))
        merged = cut

    def in_kept(x):
        return any(a <= x < b for a, b in merged)

    for ds, de in drop:
        for nd in nodes:
            if in_kept(nd.field) and ds <= nd.target < de:
                raise AssertionError(
                    f"kept fixup 0x{nd.field:X} -> 0x{nd.target:X} lands in "
                    f"drop 0x{ds:X}..0x{de:X}")

    remap = {}
    cur = 0
    for s, e in merged:
        for o in range(s, e):
            remap[o] = cur + (o - s)
        cur += e - s
    new_len = cur

    out = bytearray()
    for s, e in merged:
        out += d[s:e]

    kept_nodes = sorted((remap[nd.field], remap[nd.target])
                        for nd in nodes if nd.field in remap)
    for i, (no, nt) in enumerate(kept_nodes):
        nxt = (kept_nodes[i + 1][0] // 4) if i + 1 < len(kept_nodes) else 0xFFFF
        struct.pack_into(">HH", out, no, nxt, nt // 4)
    new_head = kept_nodes[0][0]

    # verify chain integrity (hard - these mean a broken output)
    v = walk_chain(out, new_head)
    assert len(v) == len(kept_nodes), \
        f"rebuilt chain visits {len(v)} of {len(kept_nodes)} nodes"
    for nd in v:
        assert 0 <= nd.target < new_len, \
            f"node 0x{nd.field:X} -> 0x{nd.target:X} OOB"
    assert v[-1].next is None, "rebuilt chain not terminated"

    warnings = []
    for s, e in merged[:-1]:
        if (e - s) % 8:
            warnings.append(f"kept region 0x{s:X}..0x{e:X} not 8-aligned")
    old_enddl = sum(1 for i in range(0, n - 8, 8) if d[i] == 0xDF and i in remap)
    new_enddl = sum(1 for i in range(0, new_len - 8, 8) if out[i] == 0xDF)
    if new_enddl < old_enddl:
        warnings.append(
            f"ENDDL count dropped {old_enddl} -> {new_enddl} - if a kept "
            "object's DL lost its terminator, add the missing region as a root "
            "(e.g. pass --footer so p_mobjsubs is kept too)")

    report = [
        f"chain head 0x{head:X}, {len(nodes)} fixups -> {len(kept_nodes)} kept",
        "KEEP: " + ", ".join(f"0x{s:X}..0x{e:X}" for s, e in merged),
        f"{n} -> {new_len} bytes ({n - new_len} dropped), "
        f"{new_enddl} ENDDL kept",
    ] + [f"WARNING: {w}" for w in warnings]
    return GcResult(bytes(out), new_head, merged,
                    [(s, e) for s, e in _complement(merged, n)],
                    remap, new_len, n, report)


def _complement(ranges, n):
    out, prev = [], 0
    for s, e in ranges:
        if s > prev:
            out.append((prev, s))
        prev = e
    if prev < n:
        out.append((prev, n))
    return out


# ---------------------------------------------------------------- splice editor
Splice = namedtuple("Splice", "off cut data ptrs claim")
Splice.__new__.__defaults__ = ((), None)
Splice.__doc__ = """Replace ``d[off:off+cut]`` with ``data`` (bytes).

``ptrs``  [(rel, old_target)]: the 4-byte words at ``data[rel:]`` are pointers
          to ``old_target`` (an offset in the ORIGINAL file).  They become
          fixup-chain nodes in the output.
``claim`` what happens to pointers aimed exactly at ``off``.  True: they move to
          the start of the new bytes (the new bytes take the old ones' place).
          False: they keep following the original byte at ``off`` (it moves
          down past the new bytes).  None (default) = ``cut > 0``.
``cut`` and ``len(data)`` must be multiples of 8, so every later texture /
vertex block keeps its 8-byte alignment."""

SpliceResult = namedtuple(
    "SpliceResult", "data new_head remap new_len old_len report")


def splice(d, head, splices):
    """Insert / delete / replace bytes anywhere in a GE self-relocating .bin and
    rebuild the fixup chain from scratch.

    Every pointer in the file is a chain node (field -> target), so an edit is:
    splice the bytes, send every field and target through the old->new offset
    map, add the new pointers the splices bring in, then re-thread one chain in
    ascending field order (what GE emits).  Nodes whose field was cut out are
    dropped; a node whose TARGET was cut out is an error (something still
    points at bytes the edit removes).

    Returns SpliceResult; ``remap(x)`` maps an original offset (treated as a
    pointer target) to the new file, or None if it was cut.  Use it to repoint
    anything outside the file (hitbox footers, ...).
    """
    d = bytes(d)
    n = len(d)
    sp = []
    for s in splices:
        s = Splice(s.off, s.cut, bytes(s.data), tuple(s.ptrs),
                   (s.cut > 0) if s.claim is None else bool(s.claim))
        if s.off % 8 or s.cut % 8 or len(s.data) % 8:
            raise ValueError(f"splice @0x{s.off:X}: offset, cut ({s.cut}) and "
                             f"data ({len(s.data)}) must be multiples of 8")
        if not (0 <= s.off <= s.off + s.cut <= n):
            raise ValueError(f"splice @0x{s.off:X}+{s.cut} outside the file")
        sp.append(s)
    sp.sort(key=lambda s: (s.off, s.cut))

    # merge pure inserts at the same spot (same claim) in the given order
    merged = []
    for s in sp:
        p = merged[-1] if merged else None
        if (p and p.cut == 0 and s.cut == 0 and p.off == s.off
                and p.claim == s.claim):
            ptrs = p.ptrs + tuple((r + len(p.data), t) for r, t in s.ptrs)
            merged[-1] = Splice(p.off, 0, p.data + s.data, ptrs, p.claim)
            continue
        if p and (p.off + p.cut > s.off or (p.off == s.off and p.cut == s.cut)):
            raise ValueError(f"edits overlap at 0x{p.off:X} / 0x{s.off:X}")
        merged.append(s)
    sp = merged

    def remap(x, target=True):
        shift = 0
        for s in sp:
            if x < s.off:
                break
            if x == s.off and s.claim and target:
                return s.off + shift
            if x < s.off + s.cut:
                return None
            shift += len(s.data) - s.cut
        return x + shift

    out, prev, new_ptrs = bytearray(), 0, []
    for s in sp:
        out += d[prev:s.off]
        start = len(out)
        out += s.data
        new_ptrs += [(start + rel, t) for rel, t in s.ptrs]
        prev = s.off + s.cut
    out += d[prev:]
    new_len = len(out)

    nodes = walk_chain(d, head)
    links, dropped = [], 0
    for nd in nodes:
        f = remap(nd.field, target=False)
        if f is None:
            dropped += 1
            continue
        t = remap(nd.target)
        if t is None:
            raise ValueError(
                f"pointer @0x{nd.field:X} -> 0x{nd.target:X} aims inside bytes "
                f"an edit removes")
        links.append((f, t))
    for f, old_t in new_ptrs:
        t = remap(old_t)
        if t is None or not (0 <= old_t < n):
            raise ValueError(f"new pointer -> 0x{old_t:X} has no target in "
                             f"the edited file")
        links.append((f, t))

    links.sort()
    for i, (f, t) in enumerate(links):
        if f % 4 or t % 4 or f >= CHAIN_END or t >= CHAIN_END:
            raise ValueError(f"pointer @0x{f:X} -> 0x{t:X} not encodable "
                             f"(must be 4-aligned and below 0x3FFFC)")
        if i and links[i - 1][0] == f:
            raise ValueError(f"two pointers share field 0x{f:X}")
    for i, (f, t) in enumerate(links):
        nxt = (links[i + 1][0] // 4) if i + 1 < len(links) else 0xFFFF
        struct.pack_into(">HH", out, f, nxt, t // 4)
    new_head = links[0][0] if links else CHAIN_END

    v = walk_chain(out, new_head) if links else []
    assert len(v) == len(links), \
        f"rebuilt chain visits {len(v)} of {len(links)} nodes"
    assert all(0 <= nd.target < new_len for nd in v), "rebuilt chain OOB"

    report = [f"chain head 0x{head:X} -> 0x{new_head:X}, {len(nodes)} -> "
              f"{len(links)} pointers ({dropped} cut, {len(new_ptrs)} added), "
              f"{n} -> {new_len} bytes"]
    for s in sp:
        report.append(f"  @0x{s.off:X}: -{s.cut} +{len(s.data)} bytes")
    return SpliceResult(bytes(out), new_head, remap, new_len, n, report)


# ---------------------------------------------------------- display-list edits
DList = namedtuple("DList", "start entry list_id cmds")   # cmds [(off, w0, w1)]


def dobjdesc_entries(d, first):
    """Offsets of the 0x2C-byte DObjDesc entries from ``first`` up to the
    id == 0x12 terminator."""
    out, e = [], first
    while e + 4 <= len(d) and u32(d, e) != 0x12 and len(out) < 64:
        out.append(e)
        e += 0x2C
    return out


def read_dl(d, start, limit=4096):
    """[(off, w0, w1)] from ``start`` through its G_ENDDL (0xDF)."""
    cmds, o = [], start
    while o + 8 <= len(d) and len(cmds) < limit:
        w0, w1 = u32(d, o), u32(d, o + 4)
        cmds.append((o, w0, w1))
        if w0 >> 24 == 0xDF:
            return cmds
        o += 8
    raise ValueError(f"DL @0x{start:X} has no G_ENDDL")


def find_display_lists(d, head, roots):
    """Top-level display lists of the DObjDesc lists whose first entries are
    ``roots``.  An entry's dl is either a plain Gfx list (list_id None) or a
    DObjDLLink array ``{u32 list_id; Gfx *dl}`` ended by list_id 4.  G_DL calls
    into the file are followed (same entry/list).  Deduped, file order."""
    tgt = {nd.field: nd.target for nd in walk_chain(d, head)}
    out, seen = [], set()

    def add(start, entry, lid):
        if start in seen:
            return
        seen.add(start)
        dl = DList(start, entry, lid, read_dl(d, start))
        out.append(dl)
        for off, w0, _ in dl.cmds:          # G_DL into this file
            if w0 >> 24 == 0xDE and off + 4 in tgt:
                add(tgt[off + 4], entry, lid)

    for r in roots:
        for idx, e in enumerate(dobjdesc_entries(d, r)):
            p = tgt.get(e + 4)
            if p is None:
                continue
            if u32(d, p) <= 4 and (p + 4 in tgt or u32(d, p) == 4):
                while p + 8 <= len(d) and u32(d, p) != 4:
                    if p + 4 in tgt:
                        add(tgt[p + 4], idx, u32(d, p))
                    p += 8
            else:
                add(p, idx, None)
    return sorted(out, key=lambda x: x.start)


# -------------------------------------------------- old <-> new offset matching
def chain_breakpoints(old_d, old_head, new_d, new_head):
    """Pair the two files' fixup chains 1:1 in chain order and return
    [(old_field, delta), ...] where delta = new_field - old_field.  Works when a
    re-export keeps the object/struct scaffold but shifts everything by inserted
    or removed bytes (GE geometry/texture edits do exactly this).  Requires the
    same node count; raises otherwise (structure changed too much - fall back to
    a manual anchor diff)."""
    a = walk_chain(old_d, old_head)
    b = walk_chain(new_d, new_head)
    if len(a) != len(b):
        raise ValueError(
            f"fixup count differs ({len(a)} vs {len(b)}); can't auto-pair - "
            "diff a known struct signature by hand for the shift")
    bp, last = [], None
    for na, nb in zip(sorted(a), sorted(b)):
        delta = nb.field - na.field
        if delta != last:
            bp.append((na.field, delta))
            last = delta
    return bp


def translate(offset, breakpoints):
    """Map an offset from the old file to the new one using chain_breakpoints()."""
    delta = 0
    for off, d in breakpoints:
        if off <= offset:
            delta = d
        else:
            break
    return offset + delta


# ------------------------------------------- named offsets (build constants) ---
def _node_target(nodes, field):
    for n in nodes:
        if n.field == field:
            return n.target
    return None


def _trace_mobjsub(nodes, p_mobjsubs):
    """p_mobjsubs -> {NULL, &arr}; arr -> {&MObjSub, NULL};
    MObjSub{u16 pad, u8 fmt, u8 siz, void **sprites, ...}; sprites -> {&img x N}.
    -> (mobjsub, sprites, [sprite data offsets])."""
    arr = _node_target(nodes, p_mobjsubs + 4) or _node_target(nodes, p_mobjsubs)
    if arr is None:
        return None
    mobjsub = _node_target(nodes, arr)
    if mobjsub is None:
        return None
    sprites = _node_target(nodes, mobjsub + 4)
    imgs, k = [], sprites
    while sprites is not None and k is not None:
        t = _node_target(nodes, k)
        if t is None:
            break
        imgs.append(t)
        k += 4
    return (mobjsub, sprites, imgs)


def describe_offsets(model_bytes, footer_bytes=None):
    """Named byte offsets inside a GoldEditor model .bin, for build-generated
    constants that a re-export regenerates cleanly.  -> {name: offset}:

        head                 fixup-chain head
        obj0, obj1, ...       each DObjDesc list (first entry, past the preamble)
        tex0, tex1, ...       each image the display lists reference (file order)
        tlut0, tlut1, ...     each palette
        footer_dobjdesc      where <name>_hitbox.bin's footer points (in this file)
        footer_mobjsubs      "
        mobjsub, sprites     the MObjSub and its 3-entry sprite pointer table
    """
    d = bytes(model_bytes)
    head = find_chain_head(d)
    if head is None:
        return {}
    nodes = walk_chain(d, head)
    out = {"head": head}
    for i, obj in enumerate(find_objects(d)):
        out[f"obj{i}"] = obj.start + 0x10
    ti = li = 0
    for t in find_textures(d, head=head, nodes=nodes):
        if t["is_tlut"]:
            out[f"tlut{li}"] = t["off"]
            li += 1
        else:
            out[f"tex{ti}"] = t["off"]
            ti += 1
    if footer_bytes:
        fr = footer_roots(footer_bytes)
        out["footer_dobjdesc"] = fr.dobjdesc
        if fr.pmobjsubs is not None:
            out["footer_mobjsubs"] = fr.pmobjsubs
            ms = _trace_mobjsub(nodes, fr.pmobjsubs)
            if ms:
                out["mobjsub"], out["sprites"], imgs = ms
                for i, im in enumerate(imgs):
                    out[f"sprite{i}"] = im
    return out
