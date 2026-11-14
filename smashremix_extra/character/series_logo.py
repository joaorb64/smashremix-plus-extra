"""Series logo: one mesh -> 3D results/data-screen logo + CSS logo + HUD emblem.

The source is a mesh in an open format, the first found in the character
folder:
- series_logo.obj  (Wavefront OBJ; polygons are triangulated here)
- series_logo.gltf / series_logo.glb  (glTF 2.0)
- series_logo.bin: a GEE export of file 0x23 (legacy; only its triangles are
  read). `appender/scripts/series_logo.py convert` turns it into an OBJ.
Materials, UVs and colors are ignored.

Axes: Y up, the logo faces +Z (towards the viewer: the front view of OBJ /
glTF, which Blender imports facing its front camera). Units don't matter: the
logo is centered and scaled to the size of the vanilla logos (longest side
LOGO_SIZE game units).

From the mesh the build makes:
- the 3D logo (file 0x23 block): DObjDesc tree, one display list and the
  vanilla emblem material (MObjSub + light-color script with the player
  colors), so the logo always takes the player color however it was made.
- series_logo.png (CSS, I8 64x48, gray 0x90) and hud.png (HUD emblem, white),
  front silhouettes fitted the way the vanilla logos sit, when the folder has
  no hand-made PNG.
"""
from __future__ import annotations

import base64
import json
import math
import os
import struct

from PIL import Image

APPENDER = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
TEMPLATE_OBJ = os.path.join(APPENDER, "templates", "series_logo",
                            "series_logo.obj")
SOURCE_NAMES = ("series_logo.obj", "series_logo.gltf", "series_logo.glb")

LOGO_SIZE = 300.0   # longest side, game units (vanilla ~280, Remix ~300-350)

# Vanilla emblem material: FTEmblemModels Mario MObjSub[0] (file 0x23 +0x8),
# untextured, flags 0x00022205, prim white, env black; the light colors come
# from the material script below.
MOBJSUB = bytes.fromhex(
    "00000002000000000000000000000000" "00000000000000000000000000000000"
    "00000000000000000000000000000000" "30000002000000000000000000000000"
    "00000000000000000000000000022205" "ffffffff00000000000000ff00000008"
    "66666600000000000000000000000000" "0000000000000000")
assert len(MOBJSUB) == 0x78


# Player colors (light1 diffuse, light2 ambient) per color index: P1-P4, CPU
PLAYER_LIGHTS = [
    (0xB31919, 0x262626),
    (0x001999, 0x262626),
    (0xFFB300, 0x262626),
    (0x009919, 0x262626),
    (0x666666, 0x000000),
]

ROOT_TRANSLATE = (0x435B0006, 0x44214005)   # vanilla root (219, 645, 0)
F_ONE = 0x3F800000

VTX_CACHE = 32


class Vertex:
    __slots__ = ("pos", "normal")

    def __init__(self, pos, normal):
        self.pos = pos          # game units (x, y, z)
        self.normal = normal    # unit vector


# --------------------------------------------------------------- mesh sources

def _u32(d, o):
    return struct.unpack_from(">I", d, o)[0]


def _f32(d, o):
    return struct.unpack_from(">f", d, o)[0]


def _euler_matrix(rx, ry, rz):
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    # R = Rz * Ry * Rx (HAL DObj rotation order)
    return [
        [cy * cz, sx * sy * cz - cx * sz, cx * sy * cz + sx * sz],
        [cy * sz, sx * sy * sz + cx * cz, cx * sy * sz - sx * cz],
        [-sy, sx * cy, cx * cy],
    ]


def _mul(m, v):
    return tuple(m[i][0] * v[0] + m[i][1] * v[1] + m[i][2] * v[2]
                 for i in range(3))


def _normalize(v):
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return (0.0, 0.0, 1.0) if n < 1e-9 else (v[0] / n, v[1] / n, v[2] / n)


def mesh_from_gee_bin(d):
    """Triangles of a GEE series_logo.bin (whole file 0x23 export). The logo
    data is at the end: DObjDescs 0x168 bytes before the end."""
    d = bytes(d)
    n = len(d)
    dd = n - 0x168
    tris = []
    o = dd
    while o + 0x2C <= n:
        did = _u32(d, o)
        if did == 0x12:
            break
        dl_cell = _u32(d, o + 4)
        if did != 0 and dl_cell:
            t = [_f32(d, o + 8 + 4 * i) for i in range(3)]
            r = [_f32(d, o + 0x14 + 4 * i) for i in range(3)]
            s = [_f32(d, o + 0x20 + 4 * i) for i in range(3)]
            m = _euler_matrix(*r)
            tris += _decode_dl(d, (dl_cell & 0xFFFF) * 4, m, t, s)
        o += 0x2C
    if not tris:
        raise ValueError("no triangles found (not a GEE series logo export?)")
    return tris


def _decode_dl(d, o, m, t, s):
    cache = [None] * VTX_CACHE
    tris = []

    def xf(v):
        p = (v[0] * s[0], v[1] * s[1], v[2] * s[2])
        p = _mul(m, p)
        return (p[0] + t[0], p[1] + t[1], p[2] + t[2])

    while o + 8 <= len(d):
        w0, w1 = _u32(d, o), _u32(d, o + 4)
        op = w0 >> 24
        if op == 0x01:                                  # G_VTX
            cnt = (w0 >> 12) & 0xFF
            v0 = ((w0 & 0xFF) >> 1) - cnt
            src = (w1 & 0xFFFF) * 4
            for i in range(cnt):
                vo = src + i * 16
                x, y, z = struct.unpack_from(">hhh", d, vo)
                nx, ny, nz = struct.unpack_from(">bbb", d, vo + 12)
                cache[v0 + i] = Vertex(xf((x, y, z)),
                                       _normalize(_mul(m, (nx, ny, nz))))
        elif op == 0x05:                                # G_TRI1
            tris.append(tuple(cache[((w0 >> k) & 0xFF) // 2]
                              for k in (16, 8, 0)))
        elif op == 0x06:                                # G_TRI2
            tris.append(tuple(cache[((w0 >> k) & 0xFF) // 2]
                              for k in (16, 8, 0)))
            tris.append(tuple(cache[((w1 >> k) & 0xFF) // 2]
                              for k in (16, 8, 0)))
        elif op == 0xDE and (w0 >> 16) & 0xFF == 1:     # branch (no return)
            break
        elif op == 0xDF:
            break
        o += 8
    return [tr for tr in tris if None not in tr]


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _face_normal(pts):
    """Newell normal of a polygon."""
    n = [0.0, 0.0, 0.0]
    for i, p in enumerate(pts):
        q = pts[(i + 1) % len(pts)]
        n[0] += (p[1] - q[1]) * (p[2] + q[2])
        n[1] += (p[2] - q[2]) * (p[0] + q[0])
        n[2] += (p[0] - q[0]) * (p[1] + q[1])
    return _normalize(n)


def _triangulate(pts):
    """Ear clipping of a (possibly concave) planar polygon. Returns index
    triples into PTS, keeping its winding."""
    n = len(pts)
    if n == 3:
        return [(0, 1, 2)]
    nrm = _face_normal(pts)
    ax = max(range(3), key=lambda i: abs(nrm[i]))
    u, v = [(1, 2), (2, 0), (0, 1)][ax]
    sign = 1.0 if nrm[ax] >= 0 else -1.0
    p2 = [(p[u], p[v]) for p in pts]

    def cross2(o, a, b):
        return ((a[0] - o[0]) * (b[1] - o[1]) -
                (a[1] - o[1]) * (b[0] - o[0])) * sign

    def inside(p, a, b, c):
        return cross2(a, b, p) >= 0 and cross2(b, c, p) >= 0 and \
            cross2(c, a, p) >= 0

    idx = list(range(n))
    tris = []
    guard = 0
    while len(idx) > 3 and guard < n * n:
        guard += 1
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = p2[i0], p2[i1], p2[i2]
            if cross2(a, b, c) <= 0:
                continue                                # reflex corner
            if any(inside(p2[j], a, b, c) for j in idx
                   if j not in (i0, i1, i2) and p2[j] not in (a, b, c)):
                continue
            tris.append((i0, i1, i2))
            idx.pop(k)
            break
        else:
            break                                       # degenerate: fan
    if len(idx) > 3:
        tris += [(idx[0], idx[i], idx[i + 1]) for i in range(1, len(idx) - 1)]
    else:
        tris.append(tuple(idx))
    return tris


def _make_tri(pts, nrms):
    if nrms is None or None in nrms:
        fn = _normalize(_cross(_sub(pts[1], pts[0]), _sub(pts[2], pts[0])))
        nrms = [fn] * 3
    return tuple(Vertex(tuple(p), _normalize(n)) for p, n in zip(pts, nrms))


def mesh_from_obj(text):
    """Triangles of a Wavefront OBJ (v / vn / f; ngons triangulated)."""
    vs, vns, tris = [], [], []
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "v":
            vs.append(tuple(float(x) for x in parts[1:4]))
        elif parts[0] == "vn":
            vns.append(tuple(float(x) for x in parts[1:4]))
        elif parts[0] == "f":
            corners = []
            for c in parts[1:]:
                f = c.split("/")
                vi = int(f[0])
                vi = vi - 1 if vi > 0 else len(vs) + vi
                ni = None
                if len(f) > 2 and f[2]:
                    ni = int(f[2])
                    ni = ni - 1 if ni > 0 else len(vns) + ni
                corners.append((vs[vi], vns[ni] if ni is not None else None))
            if len(corners) < 3:
                continue
            pts = [c[0] for c in corners]
            for i, j, k in _triangulate(pts):
                tri = [corners[i], corners[j], corners[k]]
                nr = [c[1] for c in tri]
                tris.append(_make_tri([c[0] for c in tri],
                                      None if None in nr else nr))
    return tris


def write_obj(tris, path, frame=True):
    """Triangles -> OBJ (positions / normals deduplicated). FRAME adds a
    reference square (loose edges, ignored by the build): the box the build
    fits the logo into, centered on the logo, side = its longest side."""
    vi, ni, vl, nl, faces = {}, {}, [], [], []

    def key(t, nd):
        return tuple(round(c, nd) for c in t)
    for tri in tris:
        f = []
        for v in tri:
            pk, nk = key(v.pos, 4), key(v.normal, 4)
            if pk not in vi:
                vi[pk] = len(vl) + 1
                vl.append(pk)
            if nk not in ni:
                ni[nk] = len(nl) + 1
                nl.append(nk)
            f.append(f"{vi[pk]}//{ni[nk]}")
        faces.append("f " + " ".join(f))

    def num(x):
        return f"{x:.4f}".rstrip("0").rstrip(".") if x != int(x) \
            else str(int(x))
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("# Series logo (SR Extra): Y up, facing +Z\n")
        for p in vl:
            f.write("v " + " ".join(num(c) for c in p) + "\n")
        for n in nl:
            f.write("vn " + " ".join(num(c) for c in n) + "\n")
        f.write("o SeriesLogo\n")
        f.write("\n".join(faces) + "\n")
        if frame and tris:
            x0, y0, x1, y1 = _bounds(tris)
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            r = max(x1 - x0, y1 - y0) / 2
            f.write("o Reference_frame\n# the box the build fits the logo "
                    "into (edges only: not part of the logo)\n")
            for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                f.write(f"v {num(round(cx + sx * r, 4))} "
                        f"{num(round(cy + sy * r, 4))} 0\n")
            f.write("l -4 -3 -2 -1 -4\n")


# glTF 2.0 ------------------------------------------------------------------

_GLTF_COMP = {5120: "b", 5121: "B", 5122: "h", 5123: "H", 5125: "I",
              5126: "f"}
_GLTF_NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def _mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)]
            for i in range(4)]


def _node_matrix(node):
    if "matrix" in node:
        m = node["matrix"]                              # column-major
        return [[m[c * 4 + r] for c in range(4)] for r in range(4)]
    tx, ty, tz = node.get("translation", (0, 0, 0))
    x, y, z, w = node.get("rotation", (0, 0, 0, 1))
    sx, sy, sz = node.get("scale", (1, 1, 1))
    r = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
         [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
         [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    return [[r[0][0] * sx, r[0][1] * sy, r[0][2] * sz, tx],
            [r[1][0] * sx, r[1][1] * sy, r[1][2] * sz, ty],
            [r[2][0] * sx, r[2][1] * sy, r[2][2] * sz, tz],
            [0, 0, 0, 1]]


def mesh_from_gltf(path):
    """Triangles of a .gltf / .glb (every mesh of the default scene, node
    transforms applied)."""
    with open(path, "rb") as f:
        data = f.read()
    bin_chunk = None
    if data[:4] == b"glTF":
        o, doc = 12, None
        while o < len(data):
            ln, kind = struct.unpack_from("<II", data, o)
            chunk = data[o + 8:o + 8 + ln]
            if kind == 0x4E4F534A:
                doc = json.loads(chunk)
            elif kind == 0x004E4942:
                bin_chunk = chunk
            o += 8 + ln
    else:
        doc = json.loads(data)

    buffers = []
    for b in doc.get("buffers", []):
        uri = b.get("uri")
        if uri is None:
            buffers.append(bin_chunk)
        elif uri.startswith("data:"):
            buffers.append(base64.b64decode(uri.split(",", 1)[1]))
        else:
            with open(os.path.join(os.path.dirname(path), uri), "rb") as f:
                buffers.append(f.read())

    def accessor(i):
        acc = doc["accessors"][i]
        view = doc["bufferViews"][acc["bufferView"]]
        buf = buffers[view["buffer"]]
        fmt = _GLTF_COMP[acc["componentType"]]
        n = _GLTF_NCOMP[acc["type"]]
        size = struct.calcsize("<" + fmt)
        stride = view.get("byteStride") or size * n
        base = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
        return [struct.unpack_from("<" + fmt * n, buf, base + k * stride)
                for k in range(acc["count"])]

    tris = []

    def visit(ni, parent):
        node = doc["nodes"][ni]
        m = _mat_mul(parent, _node_matrix(node))
        nm = [row[:3] for row in m[:3]]
        if "mesh" in node:
            for prim in doc["meshes"][node["mesh"]]["primitives"]:
                if prim.get("mode", 4) != 4:
                    continue
                pos = accessor(prim["attributes"]["POSITION"])
                nrm = accessor(prim["attributes"]["NORMAL"]) \
                    if "NORMAL" in prim["attributes"] else None
                idx = [i[0] for i in accessor(prim["indices"])] \
                    if "indices" in prim else list(range(len(pos)))
                for t in range(0, len(idx) - 2, 3):
                    c = idx[t:t + 3]
                    pts = [tuple(sum(m[r][k] * (pos[i] + (1,))[k]
                                     for k in range(4)) for r in range(3))
                           for i in c]
                    nrs = None
                    if nrm:
                        # normals: inverse-transpose; uniform scale assumed
                        nrs = [_mul(nm, nrm[i]) for i in c]
                    tris.append(_make_tri(pts, nrs))
        for ch in node.get("children", []):
            visit(ch, m)

    ident = [[1 if r == c else 0 for c in range(4)] for r in range(4)]
    scenes = doc.get("scenes")
    if scenes:
        roots = scenes[doc.get("scene", 0)].get("nodes", [])
    else:
        children = {c for n in doc.get("nodes", []) for c in n.get("children", [])}
        roots = [i for i in range(len(doc.get("nodes", []))) if i not in children]
    for r in roots:
        visit(r, ident)
    return tris


def normalize(tris):
    """Center the logo (X/Y bounds, Z bounds) and scale it uniformly so its
    longest side (X or Y) is LOGO_SIZE game units."""
    xs = [v.pos[0] for t in tris for v in t]
    ys = [v.pos[1] for t in tris for v in t]
    zs = [v.pos[2] for t in tris for v in t]
    side = max(max(xs) - min(xs), max(ys) - min(ys))
    if side <= 0:
        raise ValueError("the logo mesh is flat along X and Y")
    k = LOGO_SIZE / side
    c = ((max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2,
         (max(zs) + min(zs)) / 2)
    return [tuple(Vertex(tuple((p - o) * k for p, o in zip(v.pos, c)),
                         v.normal) for v in t) for t in tris]


def load_mesh(folder):
    """(normalized triangles, source file name) of FOLDER's logo, or
    (None, None)."""
    for name in SOURCE_NAMES:
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            if name.endswith(".obj"):
                with open(path, "r", encoding="utf-8") as f:
                    tris = mesh_from_obj(f.read())
            else:
                tris = mesh_from_gltf(path)
            break
    else:
        path = os.path.join(folder, "series_logo.bin")
        if not os.path.isfile(path):
            return None, None
        name = "series_logo.bin"
        with open(path, "rb") as f:
            tris = mesh_from_gee_bin(f.read())
    if not tris:
        raise ValueError(f"{path}: no triangles")
    return normalize(tris), name


# ---------------------------------------------------------------- 3D logo

class _Block:
    """Byte buffer with relocation cells (offsets of 32-bit pointers)."""

    def __init__(self):
        self.d = bytearray()
        self.cells = {}         # cell offset -> target offset

    def align(self, n=8):
        while len(self.d) % n:
            self.d += b"\0"

    def here(self):
        return len(self.d)

    def word(self, v):
        self.d += struct.pack(">I", v)

    def ptr(self, target):
        self.cells[len(self.d)] = target
        self.d += b"\0\0\0\0"

    def finish(self):
        """Write the chain: hi16 = next cell / 4 (0xFFFF last),
        lo16 = target / 4. Returns the chain head offset."""
        cells = sorted(self.cells)
        for i, c in enumerate(cells):
            nxt = cells[i + 1] // 4 if i + 1 < len(cells) else 0xFFFF
            tgt = self.cells[c]
            assert tgt % 4 == 0
            struct.pack_into(">HH", self.d, c, nxt, tgt // 4)
        return cells[0]


def _vtx_bytes(v):
    def s16(x):
        return max(-32768, min(32767, int(round(x))))

    def s8(x):
        return max(-128, min(127, int(round(x * 127))))
    return struct.pack(">hhhHhhbbbB",
                       s16(v.pos[0]), s16(v.pos[1]), s16(v.pos[2]), 0, 0, 0,
                       s8(v.normal[0]), s8(v.normal[1]), s8(v.normal[2]),
                       0xFF)


def _batches(tris):
    """Split triangles into vertex-cache batches: [(vertex list, [(i,j,k)])]."""
    out = []
    verts, index, faces = [], {}, []
    for tri in tris:
        keys = [_vtx_bytes(v) for v in tri]
        if len(set(keys)) < 3:
            continue                                    # degenerate
        new = [k for k in dict.fromkeys(keys) if k not in index]
        if len(verts) + len(new) > VTX_CACHE:
            out.append((verts, faces))
            verts, index, faces = [], {}, []
            new = list(dict.fromkeys(keys))
        for k in new:
            index[k] = len(verts)
            verts.append(k)
        faces.append(tuple(index[k] for k in keys))
    if faces:
        out.append((verts, faces))
    return out


def build_logo(tris):
    """Logo block for file 0x23. Returns (bytes, chain head, offsets) where
    offsets = {dobjdesc, mobjsub, matanim} (block-relative)."""
    b = _Block()
    # MObjSub + per-joint MObjSub lists
    mobjsub = b.here()
    b.d += MOBJSUB
    sub_list = b.here()
    b.ptr(mobjsub)
    b.word(0)
    sub_joints = b.here()
    b.word(0)
    b.ptr(sub_list)
    # Light color script: frame N = player color N (loops)
    script = b.here()
    for i, (l1, l2) in enumerate(PLAYER_LIGHTS):
        last = i == len(PLAYER_LIGHTS) - 1
        # SetExtValAfterBlock / SetExtValAfter (light1|light2), wait 0 / 1
        b.word((0x26 if last else 0x24) << 24 | 0x0C << 16 | (1 if i else 0))
        b.word(l1 << 8)
        b.word(l2 << 8)
    b.word(0x04000002)                                  # Wait(2)
    b.word(0x1C000000)                                  # SetAnim -> start
    b.ptr(script)
    script_list = b.here()
    b.ptr(script)
    b.word(0)
    anim_joints = b.here()
    b.word(0)
    b.ptr(script_list)

    # Vertices + display list
    b.align(8)
    batches = _batches(tris)
    vtx_off = []
    for verts, _ in batches:
        vtx_off.append(b.here())
        for k in verts:
            b.d += k
    b.align(8)
    dl = b.here()

    def cmd(w0, w1):
        b.word(w0)
        b.word(w1)
    cmd(0xE7000000, 0)                                  # pipe sync
    cmd(0xD9FFFFFF, 0x00020000)                         # G_LIGHTING on
    cmd(0xFCFFFFFF, 0xFFFE7D3E)                         # shade (vanilla)
    cmd(0xDE000000, 0x0E000000)                         # material (MObj 0)
    cmd(0xD7000000, 0x00000000)                         # texture off
    for (verts, faces), vo in zip(batches, vtx_off):
        n = len(verts)
        b.word(0x01000000 | n << 12 | (n * 2))          # G_VTX v0 = 0
        b.ptr(vo)
        for i in range(0, len(faces), 2):
            f0 = faces[i]
            w0 = 0x06000000 if i + 1 < len(faces) else 0x05000000
            w0 |= f0[0] * 2 << 16 | f0[1] * 2 << 8 | f0[2] * 2
            w1 = 0
            if i + 1 < len(faces):
                f1 = faces[i + 1]
                w1 = f1[0] * 2 << 16 | f1[1] * 2 << 8 | f1[2] * 2
            cmd(w0, w1)
    cmd(0xE7000000, 0)
    cmd(0xDF000000, 0)

    # DObjDesc tree: root (no DL), logo joint, end
    dobjdesc = b.here()
    b.word(0)
    b.word(0)
    b.word(ROOT_TRANSLATE[0])
    b.word(ROOT_TRANSLATE[1])
    b.d += b"\0" * 16
    b.d += struct.pack(">III", F_ONE, F_ONE, F_ONE)
    b.word(1)
    b.ptr(dl)
    b.d += b"\0" * 24
    b.d += struct.pack(">III", F_ONE, F_ONE, F_ONE)
    b.word(0x12)
    b.d += b"\0" * 0x28
    b.align(8)
    head = b.finish()
    return bytes(b.d), head, {
        "dobjdesc": dobjdesc, "mobjsub": sub_joints, "matanim": anim_joints,
        "triangles": sum(len(f) for _, f in batches),
        "vertices": sum(len(v) for v, _ in batches),
    }


# --------------------------------------------------------------- 2D images

CSS_SIZE = (64, 48)
CSS_BOX = (16, 1, 64, 47)        # where the vanilla-style CSS logos sit
CSS_GRAY = 0x90
HUD_MAX = 28                     # vanilla HUD emblems are ~27x25
SUPERSAMPLE = 4


def _bounds(tris):
    xs = [v.pos[0] for t in tris for v in t]
    ys = [v.pos[1] for t in tris for v in t]
    return min(xs), min(ys), max(xs), max(ys)


def _coverage(tris, size, scale, ox, oy):
    """Antialiased coverage mask (L) of the front-projected triangles:
    pixel = ((x - ox) * scale, (oy - y) * scale). Each pixel is SUPERSAMPLE^2
    sample points, a point is covered when it's inside any triangle (exact,
    so the result doesn't depend on how the shape was triangulated)."""
    ss = SUPERSAMPLE
    w, h = size
    W, H = w * ss, h * ss
    hit = bytearray(W * H)
    k = scale * ss
    for t in tris:
        (x0, y0), (x1, y1), (x2, y2) = [
            ((v.pos[0] - ox) * k, (oy - v.pos[1]) * k) for v in t]
        area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
        if abs(area) < 1e-9:
            continue                                    # edge-on
        if area < 0:
            x1, y1, x2, y2 = x2, y2, x1, y1
        # sample centers (i + 0.5, j + 0.5) inside the bounding box
        i0 = max(0, math.ceil(min(x0, x1, x2) - 0.5))
        i1 = min(W - 1, math.floor(max(x0, x1, x2) - 0.5))
        j0 = max(0, math.ceil(min(y0, y1, y2) - 0.5))
        j1 = min(H - 1, math.floor(max(y0, y1, y2) - 0.5))
        for j in range(j0, j1 + 1):
            py = j + 0.5
            row = j * W
            for i in range(i0, i1 + 1):
                px = i + 0.5
                if ((x1 - x0) * (py - y0) - (y1 - y0) * (px - x0) >= 0 and
                        (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1) >= 0 and
                        (x0 - x2) * (py - y2) - (y0 - y2) * (px - x2) >= 0):
                    hit[row + i] = 255
    img = Image.frombytes("L", (W, H), bytes(hit))
    return img.resize((w, h), Image.BOX)


def render_css(tris):
    """CSS series logo (L, 64x48): gray silhouette fitted in CSS_BOX."""
    x0, y0, x1, y1 = _bounds(tris)
    bx0, by0, bx1, by1 = CSS_BOX
    scale = min((bx1 - bx0) / max(x1 - x0, 1e-6),
                (by1 - by0) / max(y1 - y0, 1e-6))
    cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
    ox = (x0 + x1) / 2 - cx / scale
    oy = (y0 + y1) / 2 + cy / scale
    mask = _coverage(tris, CSS_SIZE, scale, ox, oy)
    return mask.point(lambda a: a * CSS_GRAY // 255)


def render_hud(tris):
    """HUD emblem (RGBA white, alpha = coverage), cropped to the logo with
    its longest side HUD_MAX px."""
    x0, y0, x1, y1 = _bounds(tris)
    scale = HUD_MAX / max(x1 - x0, y1 - y0, 1e-6)
    w = max(1, math.ceil((x1 - x0) * scale))
    h = max(1, math.ceil((y1 - y0) * scale))
    # center the logo in the pixel grid
    ox = (x0 + x1) / 2 - w / 2 / scale
    oy = (y0 + y1) / 2 + h / 2 / scale
    mask = _coverage(tris, (w, h), scale, ox, oy)
    img = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    img.putalpha(mask)
    return img
