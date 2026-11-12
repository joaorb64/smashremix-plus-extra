"""Writing fighter model data: display lists and relocatable data appended to a .bin
(materials are in materials.py)."""
import struct

import numpy as np

NULL = 0xFFFF
# the game's opaque render mode + combiner (restored after a blending material)
OPAQUE = [(0xE200001C, 0xC4112078), (0xFC127E05, 0xFF17F3FF)]


def g(w0, w1=0):
    return struct.pack('>II', w0 & 0xFFFFFFFF, w1 & 0xFFFFFFFF)


def log2(n):
    return int(np.log2(n))


class Blob:
    """Data appended to a relocatable .bin: bytes + internal pointer fields."""

    def __init__(self, base):
        self.base = base
        self.b = bytearray()
        self.ptrs = []
        self.shared = {}              # identical vertex buffers / display lists stored once

    def align(self, n=8):
        while (self.base + len(self.b)) % n:
            self.b.append(0)

    def here(self):
        return self.base + len(self.b)

    def add(self, data, align=8):
        self.align(align)
        o = self.here()
        self.b += data
        return o

    def ptr(self, field_abs, target_abs):
        self.ptrs.append((field_abs, target_abs))


def append_blob(d, blob, head, chain_fn):
    d = bytearray(d) + blob.b
    nodes = chain_fn(d, head)
    tail = max(nodes) if nodes else head
    # walk to the real tail (last node in chain order)
    off, seen = head, set()
    while off not in seen:
        seen.add(off)
        nxt = struct.unpack_from('>H', d, off)[0]
        if nxt == NULL or nxt * 4 >= len(d):
            break
        off = nxt * 4
    tail = off
    for field, target in sorted(blob.ptrs):
        struct.pack_into('>HH', d, field, NULL, target // 4)
        struct.pack_into('>H', d, tail, field // 4)
        tail = field
    return d


# ---------------------------------------------------------------- display lists
class DLWriter:
    """One joint's display list + vertex buffers (drawn double-sided)."""

    def __init__(self, blob, prologue):
        self.blob = blob
        self.cmds = [prologue, g(0xD9FFFBFF, 0x00000000)]
        self.vtx_loads = []
        self.fixups = []

    def raw(self, b, fix=()):
        """fix: [(byte offset of a command in b, target)]: its w1 points at target."""
        for off, target in fix:
            self.fixups.append((len(self.cmds), off, target))
        self.cmds.append(b)

    def vtx(self, verts, v0):
        data = b''.join(verts)
        n = len(verts)
        self.vtx_loads.append((len(self.cmds), data))
        self.cmds.append(g(0x01000000 | (n << 12) | ((v0 + n) << 1), 0))

    def tris(self, tris):
        for i in range(0, len(tris), 2):
            a = tris[i]
            if i + 1 < len(tris):
                b = tris[i + 1]
                self.raw(g(0x06000000 | a[0] * 2 << 16 | a[1] * 2 << 8 | a[2] * 2,
                           b[0] * 2 << 16 | b[1] * 2 << 8 | b[2] * 2))
            else:
                self.raw(g(0x05000000 | a[0] * 2 << 16 | a[1] * 2 << 8 | a[2] * 2))

    def finish(self):
        self.raw(g(0xE7000000))
        self.raw(g(0xE3001001, 0))
        self.raw(g(0xD9FFFFFF, 0x00000400))
        self.raw(g(0xDF000000))
        blob = self.blob
        offs = []
        for _, data in self.vtx_loads:
            if ('vtx', data) not in blob.shared:
                blob.shared[('vtx', data)] = blob.add(data, 8)
            offs.append(blob.shared[('vtx', data)])
        rel, pos = {}, 0
        for i, c in enumerate(self.cmds):
            rel[i] = pos
            pos += len(c)
        ptrs = [(rel[i] + off + 4, target) for i, off, target in self.fixups]
        ptrs += [(rel[ci] + 4, offs[k]) for k, (ci, _) in enumerate(self.vtx_loads)]
        code = b''.join(self.cmds)
        key = ('dl', code, tuple(sorted(ptrs)))
        if key in blob.shared:
            return blob.shared[key]
        dl = blob.add(code, 8)
        for field, target in ptrs:
            blob.ptr(dl + field, target)
        blob.shared[key] = dl
        return dl


def vertex(p, n, st, rgba=None):
    """Vtx: position, st, and a normal (lit) or an RGBA color (rgba 0..1, unlit)."""
    p = np.round(p).astype(int)
    if np.abs(p).max() > 32767:
        raise SystemExit('vertex out of s16 range')
    s, t = (int(np.clip(round(x * 32), -32768, 32767)) for x in st)
    if rgba is not None:
        c = np.clip(np.round(np.asarray(rgba) * 255), 0, 255).astype(int)
        return struct.pack('>hhhHhhBBBB', p[0], p[1], p[2], 0, s, t, *c[:4])
    n = np.clip(np.round(np.asarray(n) * 127), -127, 127).astype(int)
    return struct.pack('>hhhHhhbbbB', p[0], p[1], p[2], 0, s, t, n[0], n[1], n[2], 255)


def emit_tris(dw, tris_by_mat, setup, limit):
    """tris_by_mat: {mat: [(wedge, wedge, wedge)]} in drawing order; a wedge is a
    key in dw.wedges or an imported cache slot ('slot', n). setup(dw, mat)
    writes the material's commands."""
    def is_slot(w):
        return isinstance(w, tuple) and w and w[0] == 'slot'
    for mat, tris in tris_by_mat.items():
        if not tris:
            continue
        setup(dw, mat)
        batch, slots, pending = [], {}, []
        for tri in tris:
            need = list(dict.fromkeys(w for w in tri if not is_slot(w) and w not in slots))
            if len(slots) + len(need) > limit:
                dw.vtx([dw.wedges[w] for w in batch], 0)
                dw.tris(pending)
                batch, slots, pending = [], {}, []
                need = list(dict.fromkeys(w for w in tri if not is_slot(w)))
            for w in need:
                slots[w] = len(batch)
                batch.append(w)
            pending.append([w[1] if is_slot(w) else slots[w] for w in tri])
        if batch:
            dw.vtx([dw.wedges[w] for w in batch], 0)
        dw.tris(pending)
