"""Series logo: one mesh -> 3D results/data-screen logo + CSS logo + HUD emblem.

The source is a mesh, from (first found in the character folder):
- series_logo.blend: a copy of appender/templates/series_logo/series_logo.blend
  with the logo mesh edited. Read through Blender (every visible mesh object,
  modifiers applied); materials are ignored.
- series_logo.bin: a GEE export of file 0x23 (legacy). Only its triangles are
  read back.

From the mesh the build makes:
- the 3D logo (file 0x23 block): DObjDesc tree, one display list and the
  vanilla emblem material (MObjSub + light-color script with the player
  colors), so the logo always takes the player color however it was made.
- series_logo.png (CSS, I8 64x48, gray 0x90) and hud.png (HUD emblem, white),
  front silhouettes fitted the way the vanilla logos sit, when the folder has
  no hand-made PNG.

Coordinates: game units = Blender world * 100, game Y up = Blender Z, game Z
(towards the camera) = Blender -Y. The template's armature (scale 0.01,
rotated 90 on X) is just that conversion; any object setup works.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shlex
import shutil
import struct
import subprocess
import tempfile

from PIL import Image

APPENDER = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
TEMPLATE_BLEND = os.path.join(APPENDER, "templates", "series_logo",
                              "series_logo.blend")
BLENDER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "series_logo_blender.py")

GAME_PER_BLENDER = 100.0

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


def mesh_from_json(path):
    """Triangles dumped by series_logo_blender.py (Blender world units)."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    tris = []
    for tri in data["triangles"]:
        verts = []
        for pos, nrm in tri:
            bx, by, bz = pos
            nx, ny, nz = nrm
            verts.append(Vertex(
                (bx * GAME_PER_BLENDER, bz * GAME_PER_BLENDER,
                 -by * GAME_PER_BLENDER),
                _normalize((nx, nz, -ny))))
        tris.append(tuple(verts))
    return tris


# ------------------------------------------------------------------ Blender

def find_blender():
    """Blender command: $BLENDER, `blender` on the PATH, the usual Windows
    install, or the Flatpak. None when not found."""
    env = os.environ.get("BLENDER")
    if env:
        return shlex.split(env)
    if shutil.which("blender"):
        return ["blender"]
    for root in (os.environ.get("ProgramFiles", ""),
                 os.environ.get("ProgramFiles(x86)", "")):
        base = os.path.join(root, "Blender Foundation") if root else ""
        if base and os.path.isdir(base):
            for sub in sorted(os.listdir(base), reverse=True):
                exe = os.path.join(base, sub, "blender.exe")
                if os.path.isfile(exe):
                    return [exe]
    if shutil.which("flatpak") and subprocess.run(
            ["flatpak", "info", "org.blender.Blender"],
            capture_output=True).returncode == 0:
        return ["flatpak", "run", "org.blender.Blender"]
    return None


def _run_blender_script(blend, args, cwd_hint):
    cmd = find_blender()
    if cmd is None:
        raise RuntimeError(
            "Blender not found (set $BLENDER, or put blender on the PATH)")
    argv = cmd + ["-b", "--factory-startup"] + \
        ([os.path.abspath(blend)] if blend else []) + \
        ["--python-exit-code", "1", "--python", BLENDER_SCRIPT, "--"] + args
    res = subprocess.run(argv, capture_output=True, text=True)
    if res.returncode:
        raise RuntimeError(f"Blender failed:\n{res.stdout[-3000:]}\n"
                           f"{res.stderr[-3000:]}")
    return res


def mesh_from_blend(blend, cache_dir):
    """Triangles of a .blend, cached in cache_dir by the .blend's hash."""
    with open(blend, "rb") as f:
        digest = hashlib.sha1(f.read()).hexdigest()
    with open(BLENDER_SCRIPT, "rb") as f:
        digest += hashlib.sha1(f.read()).hexdigest()[:8]
    os.makedirs(cache_dir, exist_ok=True)
    cache = os.path.join(cache_dir, "series_logo_mesh.json")
    if os.path.isfile(cache):
        with open(cache, "r", encoding="utf-8") as f:
            if json.load(f).get("source_hash") == digest:
                return mesh_from_json(cache)
    # Flatpak Blender can't see /tmp: dump next to the cache
    out = os.path.abspath(cache + ".tmp")
    _run_blender_script(blend, ["dump", out], cache_dir)
    with open(out, "r", encoding="utf-8") as f:
        data = json.load(f)
    os.remove(out)
    if not data["triangles"]:
        raise ValueError(f"{blend}: no visible mesh triangles")
    data["source_hash"] = digest
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return mesh_from_json(cache)


def write_blend(tris, out_blend):
    """New .blend from the template with the template mesh replaced by TRIS."""
    out_blend = os.path.abspath(out_blend)
    tmp = out_blend + ".mesh.json"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"triangles": [
            [[(v.pos[0] / GAME_PER_BLENDER, -v.pos[2] / GAME_PER_BLENDER,
               v.pos[1] / GAME_PER_BLENDER),
              (v.normal[0], -v.normal[2], v.normal[1])] for v in tri]
            for tri in tris]}, f)
    try:
        _run_blender_script(TEMPLATE_BLEND, ["make", tmp, out_blend],
                            os.path.dirname(out_blend))
    finally:
        os.remove(tmp)


def load_mesh(folder, cache_dir):
    """(triangles, source file name) of FOLDER's logo, or (None, None).
    Raises when the .blend can't be read and there's no .bin to fall back on;
    with a .bin, the error is returned as the third value."""
    blend = os.path.join(folder, "series_logo.blend")
    gee = os.path.join(folder, "series_logo.bin")
    err = None
    if os.path.isfile(blend):
        try:
            return mesh_from_blend(blend, cache_dir), "series_logo.blend", None
        except Exception as e:      # Blender missing / failing
            if not os.path.isfile(gee):
                raise
            err = e
    if os.path.isfile(gee):
        with open(gee, "rb") as f:
            return mesh_from_gee_bin(f.read()), "series_logo.bin", err
    return None, None, err


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
    pixel = ((x - ox) * scale, (oy - y) * scale)."""
    from PIL import ImageDraw
    ss = SUPERSAMPLE
    w, h = size
    img = Image.new("L", (w * ss, h * ss), 0)
    draw = ImageDraw.Draw(img)
    for t in tris:
        pts = [((v.pos[0] - ox) * scale * ss, (oy - v.pos[1]) * scale * ss)
               for v in t]
        # skip triangles seen edge-on
        area = (pts[1][0] - pts[0][0]) * (pts[2][1] - pts[0][1]) - \
            (pts[2][0] - pts[0][0]) * (pts[1][1] - pts[0][1])
        if abs(area) < 1e-6:
            continue
        draw.polygon(pts, fill=255)
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
