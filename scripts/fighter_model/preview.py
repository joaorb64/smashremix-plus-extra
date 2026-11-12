#!/usr/bin/env python3
"""Preview sheets of an export_fighter.py folder (EXPORT_DIR/previews/):
  body.png         every costume from the front, left, back, right, top, bottom
  expressions.png  every image of materials that have several (facial expressions,
                   alternate textures), shown on the model
  parts.png        every model part from the six sides, in every costume when its
                   colors change per costume
  skeleton.png     the electric-shock skeleton model(s)

    python3 preview.py EXPORT_DIR [--out DIR] [--size 200]

Software render (no Blender needed): textures per costume with their wrap
modes (repeat / mirror / clamp per axis), alpha (cut out below 50%, or
blended for materials with a blending render mode; intensity textures tinted
by their prim color), smooth per-vertex lighting from the model's normals
(light fixed to the camera, like a front light), back / front face culling per
material. Materials with several
images (facial expressions) get a row with every image. Writes EXPORT_DIR/previews/sheet.png by default.
"""
import argparse
import json
import os

import numpy as np
from PIL import Image, ImageDraw

LIGHT = np.array([0.3, 0.5, 0.8]) / np.linalg.norm([0.3, 0.5, 0.8])
LIGHT_COLOR = np.ones(3)            # the model's (set per folder from manifest.json 'lights')
AMBIENT = np.full(3, 0.5)


class Model:
    def __init__(self, folder):
        self.dir = folder
        self.gl = json.load(open(os.path.join(folder, 'model.gltf')))
        self.buf = open(os.path.join(folder, self.gl['buffers'][0]['uri']), 'rb').read()
        self.man = json.load(open(os.path.join(folder, 'manifest.json')))
        global LIGHT_COLOR, AMBIENT
        lights = self.man.get('lights') or {}
        LIGHT_COLOR = np.array([int(lights.get('light', 'FFFFFF')[i:i + 2], 16) / 255 for i in (0, 2, 4)])
        AMBIENT = np.array([int(lights.get('shadow', '808080')[i:i + 2], 16) / 255 for i in (0, 2, 4)])
        self.mats = [m['name'] for m in self.gl['materials']]
        self.tex_cache = {}

    def acc(self, i):
        a = self.gl['accessors'][i]
        bv = self.gl['bufferViews'][a['bufferView']]
        dt = {5126: '<f4', 5123: '<u2', 5125: '<u4'}[a['componentType']]
        n = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4}[a['type']]
        arr = np.frombuffer(self.buf, dt, a['count'] * n, bv.get('byteOffset', 0) + a.get('byteOffset', 0))
        return arr.reshape(a['count'], n) if n > 1 else arr

    def texture(self, mat, costume):
        """-> (rgba texture or None, flat color, wrap, blended?, (combiner, prim, env))"""
        e = self.man['materials'].get(mat, {})
        state = {int(a, 16) >> 24: int(b, 16) for a, b in e.get('state') or []}
        fc = next(((int(a, 16), int(b, 16)) for a, b in e.get('state') or [] if a.startswith('FC')), GAME_CC)
        color = lambda v: np.array([(v >> sh) & 255 for sh in (24, 16, 8, 0)], float) / 255  # noqa: E731
        geom = next((int(b, 16) for a, b in e.get('state') or [] if a.startswith('D9')), 0x20400)
        lights = {a: int(b, 16) for a, b in e.get('state') or [] if a.startswith('DB0A')}
        light = color(lights['DB0A0000'])[:3] if 'DB0A0000' in lights else LIGHT_COLOR
        amb = color(lights['DB0A0018'])[:3] if 'DB0A0018' in lights else AMBIENT
        prim, env = color(state.get(0xFA, 0xFFFFFFFF)), color(state.get(0xFB, 0xFFFFFFFF))
        cc = e.get('costume_colors') or []
        if cc:                                    # the MObj's colors for this costume
            cur = cc[min(costume, len(cc) - 1)]
            prim = color(int(cur['prim'], 16)) if 'prim' in cur else prim
            env = color(int(cur['env'], 16)) if 'env' in cur else env
            light = color(int(cur['light1'], 16))[:3] if 'light1' in cur else light
            amb = color(int(cur['light2'], 16))[:3] if 'light2' in cur else amb
        shading = (fc, prim, env, bool(geom & 0x20000), light, amb)
        blend = 0xE2 in state and state[0xE2] not in OPAQUE_MODES
        pngs = e.get('costumes') or []
        if not pngs or not pngs[min(costume, len(pngs) - 1)]:
            if e.get('untextured'):               # its combiner takes the color (prim) itself
                return None, np.full(3, 255.0), None, blend, shading
            return None, np.array(e.get('color', [200, 200, 200, 255])[:3], float), None, blend, shading
        p = pngs[min(costume, len(pngs) - 1)]
        if p not in self.tex_cache:
            self.tex_cache[p] = np.asarray(Image.open(os.path.join(self.dir, p)).convert('RGBA')).astype(float)
        return self.tex_cache[p], None, e.get('wrap'), blend, shading

    def tris(self, prefix, costume, override=None):
        out = []
        for m in self.gl['meshes']:
            if not m['name'].startswith(prefix):
                continue
            for pr in m['primitives']:
                P = self.acc(pr['attributes']['POSITION']).astype(float)
                NR = self.acc(pr['attributes']['NORMAL']).astype(float)
                VC = (self.acc(pr['attributes']['COLOR_0']).astype(float) if 'COLOR_0' in pr['attributes']
                      else np.ones((len(P), 4)))
                UV = self.acc(pr['attributes']['TEXCOORD_0']).astype(float)
                e = self.man['materials'].get(self.mats[pr['material']], {})
                cull = next((int(b, 16) & 0x600 for a, b in e.get('state') or [] if a.startswith('D9')), 0x400)
                I = self.acc(pr['indices']).reshape(-1, 3)
                tex, col, wrap, blend, shading = self.texture(self.mats[pr['material']], costume)
                if override and self.mats[pr['material']] in override:
                    tex = override[self.mats[pr['material']]]
                for t in I:
                    out.append((P[t], UV[t], tex, col, wrap, blend, (NR[t], VC[t]), cull, shading))
        return out


GAME_CC = (0xFC127E05, 0xFF17F3FF)            # the game's fighter combiner: texel x shade, then x env
OPAQUE_MODES = {0xC4112078}


def combine(cc, T, S, P, E):
    """N64 color combiner, 2-cycle: (a - b) * c + d per cycle, color and alpha.
    T: texel rgba (..., 4), S: shade rgb (..., 3); P, E: prim / env rgba (0..1).
    TEXEL1 is taken as TEXEL0, LOD fraction as 1 (texture LOD is off), noise as 0.5."""
    w0, w1 = cc
    sh = T.shape[:-1]
    one, zero = np.ones(sh + (1,)), np.zeros(sh + (1,))
    S4 = np.concatenate([S, one], -1)
    P4, E4 = np.broadcast_to(P, sh + (4,)), np.broadcast_to(E, sh + (4,))
    fields = [((w0 >> 20) & 15, (w1 >> 28) & 15, (w0 >> 15) & 31, (w1 >> 15) & 7,
               (w0 >> 12) & 7, (w1 >> 12) & 7, (w0 >> 9) & 7, (w1 >> 9) & 7),
              ((w0 >> 5) & 15, (w1 >> 24) & 15, w0 & 31, (w1 >> 6) & 7,
               (w1 >> 21) & 7, (w1 >> 3) & 7, (w1 >> 18) & 7, w1 & 7)]
    comb = np.concatenate([np.zeros(sh + (3,)), one], -1)
    for a, b, c, d, aa, ab, ac, ad in fields:
        rgb = {0: comb[..., :3], 1: T[..., :3], 2: T[..., :3], 3: P4[..., :3], 4: S4[..., :3], 5: E4[..., :3]}
        al = {0: comb[..., 3:], 1: T[..., 3:], 2: T[..., 3:], 3: P4[..., 3:], 4: S4[..., 3:], 5: E4[..., 3:]}
        z3 = np.zeros(sh + (3,))
        A = rgb.get(a, np.full(sh + (3,), 1.0) if a == 6 else (np.full(sh + (3,), 0.5) if a == 7 else z3))
        B = rgb.get(b, z3)
        C = rgb.get(c) if c < 6 else {7: al[0], 8: al[1], 9: al[2], 10: al[3], 11: al[4], 12: al[5],
                                      13: one}.get(c, zero)
        D = rgb.get(d, np.full(sh + (3,), 1.0) if d == 6 else z3)
        AA, AB, AD = (al.get(x, one if x == 6 else zero) for x in (aa, ab, ad))
        AC = {0: one, 1: al[1], 2: al[2], 3: al[3], 4: al[4], 5: al[5]}.get(ac, zero)
        comb = np.clip(np.concatenate([(A - B) * C + D, (AA - AB) * AC + AD], -1), 0, 1)
    return comb


def wrap_coord(x, n, mode):
    if mode == 'clamp':
        return np.clip(x, 0, n - 1)
    if mode == 'mirror':
        x = np.mod(x, 2 * n)
        return np.where(x >= n, 2 * n - 1 - x, x)
    return np.mod(x, n)


def render(tris, yaw, H, bounds=None, pitch=0.0):
    """Orthographic render looking along -z after rotating by yaw around y
    (and pitch around x: pi/2 = looking straight down)."""
    c, s = np.cos(yaw), np.sin(yaw)
    rot = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    cp, sp = np.cos(pitch), np.sin(pitch)
    rot = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]]) @ rot
    if bounds is None:
        allp = np.concatenate([t[0] for t in tris]) @ rot.T
        lo, hi = allp.min(0), allp.max(0)
    else:
        lo, hi = bounds
    span = max(hi[1] - lo[1], hi[0] - lo[0], 1e-6)
    scale = (H - 16) / span
    W = int((hi[0] - lo[0]) * scale) + 16
    img = np.full((H, W, 3), 58.0)
    zb = np.full((H, W), -1e9)
    # opaque first, then blended surfaces back to front (alpha blended, no depth write)
    opaque = [t for t in tris if not t[5]]
    blended = sorted((t for t in tris if t[5]), key=lambda t: (t[0] @ rot.T)[:, 2].mean())
    for P, UV, tex, col, wrap, blend, NR, cull, shading in opaque + blended:
        q = P @ rot.T
        n = np.cross(q[1] - q[0], q[2] - q[0])
        ln = np.linalg.norm(n)
        if ln == 0:
            continue
        facing = n[2] > 0                         # counter-clockwise toward the viewer
        if (cull & 0x400 and not facing) or (cull & 0x200 and facing):
            continue
        # per-vertex lighting from the model's normals (light fixed to the camera), interpolated
        if shading[3]:                            # lit: the material's (or model's) light colors
            nv = NR[0] @ rot.T
            vshade = shading[5][None, :] + shading[4][None, :] * np.clip(nv @ LIGHT, 0, None)[:, None]
        else:                                     # unlit: vertex colors
            c = NR[1][:, :3]                      # linear in the glTF: back to the game's (sRGB) values
            vshade = np.where(c <= 0.0031308, c * 12.92, 1.055 * np.clip(c, 0, 1) ** (1 / 2.4) - 0.055)
        x = (q[:, 0] - lo[0]) * scale + 8
        y = (hi[1] - q[:, 1]) * scale + 8
        x0, x1 = int(max(0, np.floor(x.min()))), int(min(W - 1, np.ceil(x.max())))
        y0, y1 = int(max(0, np.floor(y.min()))), int(min(H - 1, np.ceil(y.max())))
        if x0 > x1 or y0 > y1:
            continue
        d = (y[1] - y[2]) * (x[0] - x[2]) + (x[2] - x[1]) * (y[0] - y[2])
        if abs(d) < 1e-9:
            continue
        xs, ys = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        a = ((y[1] - y[2]) * (xs - x[2]) + (x[2] - x[1]) * (ys - y[2])) / d
        b = ((y[2] - y[0]) * (xs - x[2]) + (x[0] - x[2]) * (ys - y[2])) / d
        cc = 1 - a - b
        inside = (a >= 0) & (b >= 0) & (cc >= 0)
        if not inside.any():
            continue
        z = a * q[0, 2] + b * q[1, 2] + cc * q[2, 2]
        shade = np.minimum(a[..., None] * vshade[0] + b[..., None] * vshade[1] + cc[..., None] * vshade[2], 1.0)
        sub = zb[y0:y1 + 1, x0:x1 + 1]
        m = inside & (z > sub)
        if tex is not None:
            th, tw = tex.shape[:2]
            u = (a * UV[0, 0] + b * UV[1, 0] + cc * UV[2, 0]) * tw
            v = (a * UV[0, 1] + b * UV[1, 1] + cc * UV[2, 1]) * th
            ws, wt = (wrap or {}).get('s', 'repeat'), (wrap or {}).get('t', 'repeat')
            tx = wrap_coord(np.floor(u).astype(int), tw, ws)
            ty = wrap_coord(np.floor(v).astype(int), th, wt)
            texel = tex[ty, tx] / 255
        else:
            texel = np.concatenate([np.broadcast_to(col / 255, xs.shape + (3,)), np.ones(xs.shape + (1,))], -1)
        # the material's color combiner (two cycles), with its prim / env colors
        out = combine(shading[0], texel, shade, shading[1], shading[2])
        px, alpha = out[..., :3] * 255, out[..., 3]
        if not blend:
            m = m & (alpha >= 0.5)
        region = img[y0:y1 + 1, x0:x1 + 1]
        if blend:
            a = alpha[m][:, None]
            region[m] = px[m] * a + region[m] * (1 - a)
        else:
            region[m] = px[m]
            sub[m] = z[m]
    return Image.fromarray(img.clip(0, 255).astype(np.uint8))


def label(im, text):
    ImageDraw.Draw(im).text((4, 3), text, fill=(255, 230, 0))
    return im


def hstack(ims, pad=6, bg=(18, 18, 18)):
    W = sum(i.width for i in ims) + pad * (len(ims) + 1)
    H = max(i.height for i in ims) + 2 * pad
    out = Image.new('RGB', (W, H), bg)
    x = pad
    for i in ims:
        out.paste(i, (x, pad))
        x += i.width + pad
    return out


def vstack(ims, bg=(0, 0, 0)):
    W = max(i.width for i in ims)
    out = Image.new('RGB', (W, sum(i.height for i in ims)), bg)
    y = 0
    for i in ims:
        out.paste(i, (0, y))
        y += i.height
    return out


VIEWS = [(0.0, 0.0, 'front'), (np.pi / 2, 0.0, 'left'), (np.pi, 0.0, 'back'), (-np.pi / 2, 0.0, 'right'),
         (0.0, np.pi / 2, 'top'), (0.0, -np.pi / 2, 'bottom')]


def rotation(yaw, pitch):
    c, s = np.cos(yaw), np.sin(yaw)
    cp, sp = np.cos(pitch), np.sin(pitch)
    return np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]]) @ np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def bounds_of(tris, yaw, pitch):
    p = np.concatenate([t[0] for t in tris]) @ rotation(yaw, pitch).T
    return p.min(0), p.max(0)


def view_row(tris, size, title, frame=None):
    """One row: the six views of tris (framed on `frame` tris, default themselves)."""
    frame = frame or tris
    return hstack([label(render(tris, y, size, bounds_of(frame, y, p), pitch=p), f'{title} {n}')
                   for y, p, n in VIEWS])


def header(text, width):
    im = Image.new('RGB', (width, 18), (18, 18, 18))
    ImageDraw.Draw(im).text((6, 3), text, fill=(255, 230, 0))
    return im


def costume_varies(md, prefix):
    """Do the materials of these meshes look different in some costume?"""
    for m in md.gl['meshes']:
        if not m['name'].startswith(prefix):
            continue
        for pr in m['primitives']:
            e = md.man['materials'].get(md.mats[pr['material']], {})
            cm = e.get('costume_map') or []
            if len({tuple(x) for x in cm}) > 1:
                return True
    return False


def body_sheet(md, size):
    n = md.man['costumes']
    body0 = md.tris('hi_', 0)
    rows = [view_row(md.tris('hi_', c), size, f'costume {c}', body0) for c in range(n)]
    return vstack(rows)


def parts_sheet(md, size):
    rows = []
    for p in md.man.get('model_parts', []):
        if p['detail'] != 'hi':
            continue
        t0 = md.tris(p['object'], 0)
        if not t0:
            continue
        costumes = range(md.man['costumes']) if costume_varies(md, p['object']) else [0]
        title = f"joint {p['joint']} part {p['variant']}"
        rows.append(header(f"{title} ({p['object']})" + ('' if len(costumes) > 1 else ': same in every costume'),
                           6 * (size + 6)))
        rows += [view_row(md.tris(p['object'], c), size, f'c{c}', t0) for c in costumes]
    return vstack(rows) if rows else None


def skeleton_sheet(md, size):
    rows = []
    for sid in sorted({s['id'] for s in md.man.get('skeletons', [])}):
        t = md.tris(f'skel{sid}_', 0)
        if t:
            rows += [header(f'electric skeleton {sid}', 6 * (size + 6)), view_row(t, size, f'skel {sid}')]
    return vstack(rows) if rows else None


def expressions_sheet(md, size):
    """Every image of every material with several (facial expressions, alternate
    textures), on the model: the meshes using that material, from the front."""
    rows = []
    for name, e in md.man['materials'].items():
        imgs = e.get('images') or []
        if len(imgs) < 2:
            continue
        users = [m['name'] for m in md.gl['meshes'] if m['name'].startswith('hi_')
                 and any(md.mats[pr['material']] == name for pr in m['primitives'])]
        if not users:
            continue
        frame = [t for u in users for t in md.tris(u, 0)]
        tiles = []
        for i, rel in enumerate(imgs):
            tex = np.asarray(Image.open(os.path.join(md.dir, rel)).convert('RGBA')).astype(float)
            tris = [t for u in users for t in md.tris(u, 0, override={name: tex})]
            tiles.append(label(render(tris, 0.0, size, bounds_of(frame, 0.0, 0.0)), f'{i}'))
        per = max(1, (6 * (size + 6)) // (tiles[0].width + 6))
        rows.append(header(f'{name}: {len(imgs)} images on {", ".join(users)} (costume 0 palette)',
                           6 * (size + 6)))
        rows += [hstack(tiles[k:k + per]) for k in range(0, len(tiles), per)]
    return vstack(rows) if rows else None


def sheets(folder, size=200):
    """{name: image} for every sheet that has something to show."""
    md = Model(folder)
    out = {'body': body_sheet(md, size), 'expressions': expressions_sheet(md, size // 2 + 30),
           'parts': parts_sheet(md, size // 2 + 30), 'skeleton': skeleton_sheet(md, size)}
    return {k: v for k, v in out.items() if v is not None}


def save_sheets(folder, out_dir=None, size=200):
    out_dir = out_dir or os.path.join(folder, 'previews')
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for k, im in sheets(folder, size).items():
        paths.append(os.path.join(out_dir, f'{k}.png'))
        im.save(paths[-1])
    return paths


def sheet(folder, size=200):
    """Everything in one image (body, expressions, parts, skeleton)."""
    return vstack(list(sheets(folder, size).values()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('folder')
    ap.add_argument('--out', help='output folder (default EXPORT_DIR/previews)')
    ap.add_argument('--size', type=int, default=200)
    a = ap.parse_args()
    for p in save_sheets(a.folder, a.out, a.size):
        print('wrote', p)


if __name__ == '__main__':
    main()
