"""Rebuild fighter materials from the exported PNGs (and whatever was edited).

A material keeps what the original had: its flags, image list / palette list
(lengths and order, so facial expressions, extra palettes and costume variants
keep working), where each comes from (the MObj's list, or loaded inline in the
DL), its costume script (which entries each costume uses), wrap modes and
render state (blending, combiner, colors: e.g. alpha-blended IA effects).

Per entry:
  * unedited               -> original bytes, untouched
  * <mat>_pal<p>.png edited -> palette p read back (colors at the pixels of the
                              reference image's indices)
  * <mat>_img<i>.png edited -> image i, mapped onto its palette's colors (it must
                              keep working with every palette it's used with)
  * costume PNG (Blender)   -> a recolor updates that costume's palette; a
                              repaint updates its image (palette colors only)
Non-CI formats (IA4/8/16, I4/8, RGBA16) are re-encoded from the PNG.
Materials that aren't in the manifest (made in Blender) become CI4 from their
costume images (16 colors).
"""
import os
import struct

import numpy as np
from PIL import Image

import fighter_files as FF

FORMATS = {'RGBA16': (0, 2), 'RGBA32': (0, 3), 'CI4': (2, 0), 'CI8': (2, 1), 'IA4': (3, 0), 'IA8': (3, 1),
           'IA16': (3, 2), 'I4': (4, 0), 'I8': (4, 1)}
WRAP_BITS = {'repeat': 0, 'mirror': 1, 'clamp': 2}
CULL = {'none': 0, 'back': 0x400, 'front': 0x200, 'both': 0x600}
FILTER = {'point': 0x0000, 'bilinear': 0x2000, 'average': 0x3000}     # G_SETOTHERMODE_H texture filter
FILTER_CMD = 0xE3001201
BPP = (4, 8, 16, 32)
TOL = 8                       # color difference still counted as "the same" (5-bit rounding)
XLU_ON = [(0xE200001C, 0xC4113878), (0xFC121805, 0xFF17FFFF)]
OPAQUE_MODE = (0xE200001C, 0xC4112078)
# render modes that only exist to make cut-out (1-bit alpha) textures transparent; GE / Remix
# models use them. They override the game's own mode (opaque, or translucent while cloaked /
# fading), so the cloaking device can't fade those parts.
CUTOUT_MODES = {0xC4113878, 0xC4113078, 0xC41049D8, 0xC4112078}   # (+ the game's own opaque mode)
# cut-out without a render mode: alpha compare (othermode L bits 0-1) against blend alpha 8.
# The game's combiner already outputs texel alpha x env alpha, so cloaking still fades them.
ALPHA_COMPARE_ON = [(0xF9000000, 0x00000008), (0xE2001E01, 0x00000001),   # blend alpha 8, like the game's own
                    (0xE2001300, 0x00001000)]
# + CVG_X_ALPHA alone (render mode bit 12, set without touching the rest of the game's mode):
# coverage x texture alpha, the texture-edge cut-out the game's own TEX_EDGE modes use. Needed
# because the game's opaque mode has ALPHA_CVG_SEL, which makes alpha compare look at coverage.
ALPHA_COMPARE_OFF = [(0xE2001E01, 0x00000000), (0xE2001300, 0x00000000)]


def g(w0, w1=0):
    return struct.pack('>II', w0 & 0xFFFFFFFF, w1 & 0xFFFFFFFF)


def rgba5551(c):
    r, gg, b, a = (int(x) for x in c[:4])
    return 0 if a < 128 else ((r >> 3) << 11 | (gg >> 3) << 6 | (b >> 3) << 1 | 1)


def decode_pal(raw):
    return np.array([FF.rgba16(v) for v in struct.unpack(f'>{len(raw) // 2}H', raw)], np.uint8)


def encode_pal(pal):
    return b''.join(struct.pack('>H', rgba5551(c)) for c in pal)


def pack(vals, bpp):
    v = np.asarray(vals, np.uint32).ravel()
    if bpp == 4:
        if len(v) % 2:
            v = np.append(v, 0)
        return bytes(((v[0::2] << 4) | v[1::2]).astype(np.uint8))
    if bpp == 8:
        return bytes(v.astype(np.uint8))
    if bpp == 16:
        return struct.pack(f'>{len(v)}H', *(int(x) for x in v))
    return struct.pack(f'>{len(v)}I', *(int(x) for x in v))


def unpack(raw, bpp, n):
    if bpp == 4:
        b = np.frombuffer(raw, np.uint8)
        out = np.empty(len(b) * 2, np.int64)
        out[0::2], out[1::2] = b >> 4, b & 15
        return out[:n]
    if bpp == 8:
        return np.frombuffer(raw[:n], np.uint8).astype(np.int64)
    return np.array(struct.unpack(f'>{n}H', raw[:2 * n]), np.int64)


def encode_direct(rgba, fmt, siz):
    """Non-CI formats from RGBA (h, w, 4)."""
    a = np.asarray(rgba).reshape(-1, 4).astype(np.int64)
    i = np.clip(np.round(a[:, :3] @ [0.299, 0.587, 0.114]), 0, 255).astype(np.int64)
    al = a[:, 3]
    if (fmt, siz) == (0, 2):
        return pack(np.where(al >= 128, (a[:, 0] >> 3) << 11 | (a[:, 1] >> 3) << 6 | (a[:, 2] >> 3) << 1 | 1, 0), 16)
    if (fmt, siz) == (3, 0):
        return pack(((i >> 5) << 1) | (al >= 128), 4)
    if (fmt, siz) == (3, 1):
        return pack(((i >> 4) << 4) | (al >> 4), 8)
    if (fmt, siz) == (3, 2):
        return pack((i << 8) | al, 16)
    if (fmt, siz) == (4, 0):
        return pack(i >> 4, 4)
    if (fmt, siz) == (4, 1):
        return pack(i, 8)
    raise SystemExit(f'texture format {fmt}/{siz} is not supported')


def clean(rgba):
    """Transparent pixels -> (0,0,0,0) so their (invisible) color never counts."""
    a = np.asarray(rgba, np.int64).copy()
    a[a[..., 3] < 128] = 0
    a[a[..., 3] >= 128, 3] = 255
    return a


def nearest_index(rgba, pal):
    px = clean(rgba).reshape(-1, 4)
    pp = clean(pal)
    d = ((px[:, None, :] - pp[None, :, :]) ** 2).sum(-1)
    k = d.argmin(1)
    return k, (np.sqrt(d[np.arange(len(k)), k].max()) if len(k) else 0)


def same_image(a, b):
    a, b = np.asarray(a), np.asarray(b)
    return a.shape == b.shape and np.abs(clean(a) - clean(b)).max() <= TOL


def first_seen(rgba, n):
    """RGBA -> (indices, palette, reduced) with <= n colors. First-seen order
    keeps recolors of one drawing index-identical (they share the image)."""
    a = clean(rgba).astype(np.uint8)
    flat = a.reshape(-1, 4)
    cols, first, inv = np.unique(flat, axis=0, return_index=True, return_inverse=True)
    reduced = len(cols) > n
    if reduced:
        q = Image.fromarray(a, 'RGBA').quantize(n, method=Image.Quantize.FASTOCTREE)
        a = clean(np.asarray(q.convert('RGBA'))).astype(np.uint8)
        flat = a.reshape(-1, 4)
        cols, first, inv = np.unique(flat, axis=0, return_index=True, return_inverse=True)
    order = np.argsort(first)
    rank = np.empty_like(order)
    rank[order] = np.arange(len(order))
    pal = np.zeros((n, 4), np.uint8)
    pal[:len(cols)] = cols[order]
    return rank[inv.ravel()], pal, reduced


def resize(im, w, h):
    im = np.asarray(im, np.uint8)
    if im.shape[:2] == (h, w):
        return im
    return np.asarray(Image.fromarray(im, 'RGBA').resize((w, h), Image.NEAREST))


class Material:
    """One rebuilt material: encoded image / palette lists + how it's drawn."""

    def __init__(self, name, entry, base, root, costume_imgs, props, costumes, cloak_safe=True):
        self.name = name
        self.cloak_safe = cloak_safe
        self.cutout = False
        e = entry or {}
        self.warnings = []
        self.new = not entry
        self.untextured = bool(e.get('untextured')) or (not entry and all(
            im is None or (im.shape[:2] == (4, 4) and (im == im[0, 0]).all()) for im in costume_imgs))
        self.costume_colors = None
        if self.untextured:
            self._init_untextured(e, props, costume_imgs, costumes)
            return
        fmt_name = props.get('n64_format') or e.get('format') or 'CI4'
        if fmt_name == 'RGBA32':
            self.warnings.append('RGBA32 is stored as RGBA16')
            fmt_name = 'RGBA16'
        if fmt_name not in FORMATS:
            raise SystemExit(f'{name}: unknown format {fmt_name} (use one of {", ".join(FORMATS)})')
        self.fmt, self.siz = FORMATS[fmt_name]
        self.format = fmt_name
        self.ci = self.fmt == 2
        if e.get('size'):
            self.w, self.h = e['size']
        else:
            self.h, self.w = costume_imgs[0].shape[:2]
        w, h = self.w, self.h
        if (w & (w - 1) or h & (h - 1)) and (w * BPP[self.siz] // 8) % 8:
            raise SystemExit(f'{name}: {w}x{h} {fmt_name}: sizes that aren\'t powers of two need rows of a multiple '
                             'of 8 bytes')
        self.stride = FF.row_bytes(w, self.siz)        # rows are stored 64-bit aligned
        nbytes = self.stride * h
        if nbytes > (2048 if self.ci else 4096):
            raise SystemExit(f'{name}: {w}x{h} {fmt_name} does not fit the texture memory')
        wrap = e.get('wrap') or {}
        self.masks = (wrap['mask_s'], wrap['mask_t']) if 'mask_s' in wrap and 'mask_t' in wrap else None
        self.wrap_s = props.get('n64_wrap_s') or wrap.get('s', 'clamp')
        self.wrap_t = props.get('n64_wrap_t') or wrap.get('t', 'clamp')
        for ax in (self.wrap_s, self.wrap_t):
            if ax not in WRAP_BITS:
                raise SystemExit(f'{name}: wrap mode {ax!r} (use repeat, mirror or clamp)')
        self._init_state(e, props)
        self.flags = e.get('flags', 0x05)
        self.costume_colors = e.get('costume_colors')      # MObj colors per costume (kept)
        self.mobj = e.get('mobj')
        self.image_source = e.get('image_source', 'list')
        self.palette_source = e.get('palette_source', 'list') if self.ci else None
        n_pal = e.get('palette_colors') or (256 if self.siz == 1 else 16)    # colors the TLUT load reads
        self.n_pal = n_pal
        bpp = BPP[self.siz]
        same_fmt = e.get('fmt') == self.fmt and e.get('siz') == self.siz
        if e and not same_fmt:
            self.warnings.append(f"format changed {e.get('format')} -> {fmt_name}: rebuilt from the costume images")
            self.new = True
        self.orig_transparent = False
        if self.new:
            self._build_new(costume_imgs, costumes, n_pal)
            self._finish()
            return

        def png(rel):
            p = os.path.join(root, rel) if rel else None
            if not p or not os.path.exists(p):
                return None
            return resize(np.asarray(Image.open(p).convert('RGBA')), w, h)

        self.costume_map = [list(m) for m in e.get('costume_map') or [[0, 0]]]
        while len(self.costume_map) < costumes:
            self.costume_map.append(self.costume_map[-1])
        self.costume_map = self.costume_map[:costumes]
        img_addrs = list(e.get('image_addrs') or [])
        pal_addrs = list(e.get('palette_addrs') or []) if self.ci else []
        img_raw = [bytes(base[a:a + nbytes]) for a in img_addrs]
        pal_raw = [bytes(base[a:a + 2 * n_pal]) for a in pal_addrs]
        # current state: CI -> index arrays + palettes; others -> RGBA arrays
        if self.ci:
            pals = [decode_pal(r) for r in pal_raw]
            imgs = [unpack(self.unpad(r), bpp, w * h) for r in img_raw]
        else:
            pals = []
            imgs = [self._decode(r) for r in img_raw]
        self.orig_transparent = self._uses_transparency(img_raw, pal_raw)
        orig_imgs = [x.copy() for x in imgs]
        orig_pals = [x.copy() for x in pals]
        ref_i, ref_p = self.costume_map[0][0], (self.costume_map[0][1] if self.ci else 0)
        # palette PNGs (drawn with the reference image)
        for p, rel in enumerate((e.get('palettes') or [])[:len(pals)]):
            im = png(rel)
            if im is not None:
                pals[p] = self._palette_from(im, imgs[ref_i], pals[p], f'palette {p}')
        # image PNGs (drawn with the reference palette)
        for i, rel in enumerate((e.get('images') or [])[:len(imgs)]):
            im = png(rel)
            if im is not None:
                imgs[i] = self._image_from(im, imgs[i], pals[ref_p] if self.ci else None, f'image {i}')
        # costume images from Blender: override the entries each costume uses
        for c, (ti, pi) in enumerate(self.costume_map):
            im = costume_imgs[c] if c < len(costume_imgs) else None
            if im is None:
                continue
            im = resize(im, w, h)
            # not painted in Blender (still the exported costume image): the entry PNGs decide
            orig = orig_imgs[ti] if not self.ci else orig_pals[pi][orig_imgs[ti]].reshape(h, w, 4)
            if same_image(orig, im):
                continue
            if not self.ci:
                imgs[ti] = im
                continue
            cols = self._recolor(im, imgs[ti])
            if cols is not None:                                       # same drawing, new colors: palette pi
                for k, col in cols.items():
                    if not same_image(col[None], pals[pi][k][None]):
                        pals[pi][k] = col
            else:                                                      # repainted: image ti, palette pi's colors
                imgs[ti] = self._image_from(im, imgs[ti], pals[pi], f'costume {c}')
        # encode (unchanged entries keep their exact bytes)
        if self.ci:
            self.images = [self.pad(pack(ix, bpp)) for ix in imgs]
            self.palettes = [r if np.array_equal(decode_pal(r), p) else encode_pal(p) for r, p in zip(pal_raw, pals)]
        else:
            self.images = []
            for r, im in zip(img_raw, imgs):
                enc = self.pad(encode_direct(im, self.fmt, self.siz))
                self.images.append(r if enc == self.pad(encode_direct(self._decode(r), self.fmt, self.siz)) else enc)
            self.palettes = []
        self._finish()

    def pad(self, packed):
        """Tightly packed rows -> rows of self.stride bytes (64-bit aligned, like the game's)."""
        row = self.w * BPP[self.siz] // 8
        if row == self.stride:
            return packed
        return b''.join(packed[y * row:(y + 1) * row].ljust(self.stride, b'\0') for y in range(self.h))

    def unpad(self, raw):
        row = self.w * BPP[self.siz] // 8
        if row == self.stride:
            return raw
        return b''.join(raw[y * self.stride:y * self.stride + row] for y in range(self.h))

    def _decode(self, raw):
        return FF.decode_texture(raw + b'\0' * 8, 0, self.fmt, self.siz, self.w, self.h, None)

    def _init_state(self, e, props):
        name = self.name
        st = props.get('n64_state')
        if isinstance(st, str):
            st = [s.split() for s in st.replace(',', ' ').split(';') if s.strip()]
        self.state = [(int(a, 16), int(b, 16)) for a, b in (st if st is not None else e.get('state') or [])]
        # geometry mode per material: culling + lighting (original's; n64_cull / n64_lighting change
        # them; new materials: back culling, lit = the game's defaults)
        cull = props.get('n64_cull')
        if cull is not None and cull not in CULL:
            raise SystemExit(f'{name}: n64_cull {cull!r} (use {", ".join(CULL)})')
        lighting = props.get('n64_lighting')
        if lighting is not None and lighting not in ('on', 'off'):
            raise SystemExit(f'{name}: n64_lighting {lighting!r} (use on or off)')
        old_geom = next((b for a, b in self.state if a >> 24 == 0xD9),
                        next((int(b, 16) for a, b in e.get('state') or [] if a.startswith('D9')), None))
        geom = FF.GEOM_DEFAULT if old_geom is None else old_geom & FF.GEOM_BITS
        if cull is not None:
            geom = (geom & ~0x600) | CULL[cull]
        if lighting is not None:
            geom = (geom & ~FF.G_LIGHTING) | (FF.G_LIGHTING if lighting == 'on' else 0)
        self.state = [s for s in self.state if s[0] >> 24 != 0xD9] + [(FF.GEOM_CMD, geom)]
        self.cull = next(k for k, v in CULL.items() if v == geom & 0x600)
        self.lit = bool(geom & FF.G_LIGHTING)
        # texture filtering: bilinear is the game's (not written); point / average are
        filt = props.get('n64_filter')
        if filt is not None and filt not in FILTER:
            raise SystemExit(f'{name}: n64_filter {filt!r} (use {", ".join(FILTER)})')
        if filt is not None:
            self.state = [s for s in self.state if s[0] != FILTER_CMD]
            if filt != 'bilinear':
                self.state.append((FILTER_CMD, FILTER[filt]))
        # its own light / shadow colors (else the model's)
        for key, words in (('n64_light_color', (0xDB0A0000, 0xDB0A0004)), ('n64_shadow_color', (0xDB0A0018, 0xDB0A001C))):
            if props.get(key):
                rgb = int(str(props[key]).lstrip('#'), 16) & 0xFFFFFF
                self.state = [s for s in self.state if s[0] not in words] + [(w, (rgb << 8) | 0xFF) for w in words]

    def _init_untextured(self, e, props, costume_imgs, costumes):
        """No texture: an MObj supplies its colors (prim / env / lights), per costume through
        its costume script. Blender: the costume materials' base colors are the prim colors."""
        self.ci, self.w, self.h, self.fmt, self.siz, self.stride = False, 0, 0, 0, 0, 0
        self.images, self.palettes, self.transparent, self.orig_transparent = [], [], False, False
        self.image_source = self.palette_source = None
        self.mobj = e.get('mobj')
        self.flags = e.get('flags', 0x200)
        self.costume_map = [[0, 0]] * costumes
        if not e:                                   # new: prim color x shading
            e = dict(state=[['FC327E05', 'FF17FDFF']])
        self._init_state(e, props)
        cols = [dict(x) for x in (e.get('costume_colors') or [])] or [{} for _ in range(costumes)]
        while len(cols) < costumes:
            cols.append(dict(cols[-1]))
        for c in range(costumes):
            im = costume_imgs[c] if c < len(costume_imgs) else None
            if im is None:
                continue
            r, g_, b = (int(x) for x in im[0, 0, :3])
            old = cols[c].get('prim')
            if old is None or max(abs(int(old[i:i + 2], 16) - v) for i, v in ((0, r), (2, g_), (4, b))) > 2:
                cols[c]['prim'] = f'{r:02X}{g_:02X}{b:02X}' + (old[6:8] if old else 'FF')
        self.costume_colors = cols[:costumes]
        varies = any(len({x.get(k) for x in self.costume_colors}) > 1 for k in COLOR_TRACKS)
        # an MObj only when there is one already, or the color changes per costume
        self.needs_mobj = self.mobj is not None or varies
        if self.needs_mobj:
            if any('prim' in x for x in self.costume_colors):
                self.flags |= 0x200
        else:                                       # the color goes in the DL (prim color)
            self.flags = 0
            prim = self.costume_colors[0].get('prim') if self.costume_colors else None
            if prim:
                self.state = [x for x in self.state if x[0] >> 24 != 0xFA] + [(0xFA000000, int(prim, 16))]
            if not any(a >> 24 == 0xFC for a, _ in self.state):
                self.state.append((0xFC327E05, 0xFF17FDFF))           # prim x shade
            self.costume_colors = None
        self.tex_varies = self.pal_varies = False

    # ---------------------------------------------------------------- building
    def _build_new(self, costume_imgs, costumes, n_pal):
        imgs = [resize(costume_imgs[min(c, len(costume_imgs) - 1)], self.w, self.h) for c in range(costumes)]
        img_at, pal_at = {}, {}
        self.images, self.palettes, self.costume_map = [], [], []
        for c, im in enumerate(imgs):
            if self.ci:
                ix, pal, reduced = first_seen(im, n_pal)
                if reduced:
                    self.warnings.append(f'costume {c}: more than {n_pal} colors, reduced')
                data, pdata = self.pad(pack(ix, BPP[self.siz])), encode_pal(pal)
            else:
                data, pdata = self.pad(encode_direct(im, self.fmt, self.siz)), None
            ti = img_at.setdefault(data, len(img_at))
            if ti == len(self.images):
                self.images.append(data)
            pi = 0
            if pdata is not None:
                pi = pal_at.setdefault(pdata, len(pal_at))
                if pi == len(self.palettes):
                    self.palettes.append(pdata)
            self.costume_map.append([ti, pi])
        # the same in every costume: loaded inline (no MObj); else from the MObj's lists
        self.image_source = 'list' if len(self.images) > 1 else 'inline'
        self.palette_source = ('list' if len(self.palettes) > 1 else 'inline') if self.ci else None
        self.flags = (0x01 if self.image_source == 'list' else 0) | (0x04 if self.palette_source == 'list' else 0)

    def _finish(self):
        tex_ids = {m[0] for m in self.costume_map}
        pal_ids = {m[1] for m in self.costume_map}
        self.tex_varies = len(tex_ids) > 1
        self.pal_varies = self.ci and len(pal_ids) > 1
        if self.tex_varies:                                            # per-costume images need the MObj list
            self.image_source = 'list'
        if self.siz == 1 and self.ci and self.image_source == 'list':
            # the MObj's own TLUT load is 16 colors when its image SETTIMG is 16b (needed by the load)
            if self.tex_varies:
                raise SystemExit(f'{self.name}: CI8 images can\'t change per costume (only palettes); use CI4')
            self.image_source = 'inline'
        if self.pal_varies:
            self.palette_source = 'list'
        self.transparent = self._uses_transparency(self.images, self.palettes)
        binary_alpha = self.ci or (self.fmt, self.siz) in ((0, 2), (3, 0))
        modes = [b for a, b in self.state if a == 0xE200001C]
        if self.cloak_safe and binary_alpha and all(m in CUTOUT_MODES for m in modes) and \
                (self.transparent or modes):
            # cut-out without its own render mode (keeps the game's: cloaking works)
            self.state = [s for s in self.state if s[0] != 0xE200001C]
            if self.transparent:
                self.cutout = True
                self.state = [s for s in self.state if s[0] >> 24 != 0xFC] + [(0xFC127E05, 0xFF17F3FF)]
        elif self.transparent and not self.orig_transparent and not self.special():
            # transparency without a blend setup (new material, or newly transparent): GE's setup
            self.state = [s for s in self.state if s[0] >> 24 not in (0xE2, 0xFC)] + XLU_ON

    def _uses_transparency(self, images, palettes):
        """Does any costume's image use a transparent texel (alpha < 50%)?"""
        n = self.w * self.h
        for ti, pi in self.costume_map:
            if ti >= len(images):
                continue
            raw = images[ti]
            if self.ci:
                if pi >= len(palettes):
                    continue
                pal = struct.unpack(f'>{len(palettes[pi]) // 2}H', palettes[pi])
                used = np.unique(unpack(self.unpad(raw), BPP[self.siz], n))
                if any(pal[k] & 1 == 0 for k in used if k < len(pal)):
                    return True
            elif self.fmt in (0, 3):
                if (self._decode(raw)[..., 3] < 128).any():
                    return True
        return False

    def _palette_from(self, im, ref_idx, old, what):
        pal = old.copy()
        flat = im.reshape(-1, 4)
        for k in np.unique(ref_idx):
            sel = clean(flat[ref_idx == k])
            if np.abs(sel - sel[0]).max() > TOL:
                self.warnings.append(f'{what}: index {k} painted with several colors; using the first one')
            if not same_image(sel[:1], pal[k][None]):
                pal[k] = sel[0]
        return pal

    def _image_from(self, im, old, pal, what):
        if pal is None:                                                 # direct color
            return old if same_image(im, old) else im
        if same_image(pal[old].reshape(im.shape), im):
            return old                                                  # unedited
        ix, err = nearest_index(im, pal)
        if err > 24:
            self.warnings.append(f'{what}: colors not in the palette were mapped to the nearest one '
                                 '(edit the palette PNGs to change colors)')
        return ix

    @staticmethod
    def _recolor(im, idx):
        """If `im` is the drawing `idx` in other colors: {index: color}; else None."""
        flat = clean(im).reshape(-1, 4)
        cols = {}
        for k in np.unique(idx):
            sel = flat[idx == k]
            if np.abs(sel - sel[0]).max() > TOL:
                return None
            cols[int(k)] = sel[0].astype(np.uint8)
        return cols

    # ---------------------------------------------------------------- drawing
    def uses_mobj(self):
        if self.untextured:
            return self.needs_mobj
        return self.image_source == 'list' or self.palette_source == 'list' or \
            bool(self.flags & 0x3E00 and self.costume_colors)

    def special(self):
        """Sets its own render mode (blending): drawn last in its display list."""
        return any(w0 >> 24 == 0xE2 for w0, _ in self.state)

    def mobj_flags(self):
        f = self.flags
        f = (f | 0x01) if self.image_source == 'list' else (f & ~0x01)
        f = (f | 0x04) if self.palette_source == 'list' else (f & ~0x04)
        return f

    def block(self, mobj_index):
        """DL commands that set this material up -> (bytes, [(offset, 'image'|'palette')]);
        inline image / palette loads need their pointer (at offset + 4) filled in."""
        if self.untextured:                      # texture off; its colors: the MObj's, or prim in the state
            return g(0xD7000000, 0) + (g(0xDE000000, 0x0E000000 + 8 * mobj_index) if self.needs_mobj else b''), []
        w, h, fmt, siz = self.w, self.h, self.fmt, self.siz
        cms, cmt = WRAP_BITS[self.wrap_s], WRAP_BITS[self.wrap_t]
        ms, mt = int(np.ceil(np.log2(w))), int(np.ceil(np.log2(h)))      # masks (rounded up: clamped edges)
        if self.masks:                                                  # the original's (may repeat a part)
            ms, mt = self.masks
        load_siz = 3 if siz == 3 else 2
        bpl = self.stride                                  # bytes per row (64-bit aligned)
        line = bpl >> 3
        words = max(1, bpl // 8)
        dxt = (2048 + words - 1) // words
        lrs = bpl * h // 2 - 1 if siz < 3 else bpl * h // 4 - 1   # in load (16 / 32-bit) texels
        tile = (cmt << 18) | (mt << 14) | (cms << 8) | (ms << 4)
        out, fix = [], []                                              # (render state: state_commands())
        out.append(g(0xE3001001, 0x8000 if self.ci else 0))         # TLUT (RGBA16) for CI only
        out.append(g(0xE8000000))
        out.append(g(0xF5000100, 0x05000000))                       # TLUT tile 5 @ tmem 0x100
        out.append(g(0xF5000000 | (fmt << 21) | (load_siz << 19), 0x07000000 | tile))       # load tile 7
        out.append(g(0xF5000000 | (fmt << 21) | (siz << 19) | (line << 9), tile))         # render tile 0
        out.append(g(0xE6000000))
        out.append(g(0xD7000002, 0xFFFFFFFF))
        out.append(g(0xF2000000, ((w - 1) << 2) << 12 | ((h - 1) << 2)))
        if self.uses_mobj():
            out.append(g(0xDE000000, 0x0E000000 + 8 * mobj_index))   # MObj: its palette / image list entry
        # the MObj loads the TLUT itself only together with its image (flags 0x1); otherwise
        # it just points at the palette and the DL loads it (like the game's eye materials)
        if self.ci and (self.palette_source != 'list' or self.image_source != 'list'):
            if self.palette_source != 'list':
                fix.append((len(out) * 8, 'palette'))
                out.append(g(0xFD100000, 0))                         # palette (RGBA16)
            out.append(g(0xE8000000))
            out.append(g(0xF5000100, 0x05000000))
            out.append(g(0xE6000000))
            out.append(g(0xF0000000, 0x05000000 | ((self.n_pal - 1) << 14)))
            out.append(g(0xE7000000))
        if self.image_source != 'list':
            fix.append((len(out) * 8, 'image'))
            out.append(g(0xFD000000 | (fmt << 21) | (load_siz << 19), 0))
        out.append(g(0xE6000000))
        out.append(g(0xF3000000, 0x07000000 | (min(lrs, 2047) << 12) | dxt))
        out.append(g(0xE7000000))
        return b''.join(out), fix

    def state_commands(self):
        """The render state this material draws with: [(w0, w1)] (blend mode, combiner, colors,
        geometry mode, lights). The DL writer emits only what differs from what's already set."""
        st = [(a, b) for a, b in self.state if a != 0xE3001001]       # (the TLUT mode is the block's)
        if self.cutout:
            st += ALPHA_COMPARE_ON
        if not any(a >> 24 == 0xFA for a, _ in st):
            st.append((0xFA000000, 0xFFFFFFFF))
        return st

    def inline_data(self, what):
        return self.images[self.costume_map[0][0]] if what == 'image' else self.palettes[self.costume_map[0][1]]

    def script(self, costumes):
        """Costume script: per costume, the texture / palette index (flags 0x001 / 0x200) and
        MObj colors (color events, flag bits 0-4 = prim / env / blend / light1 / light2) that
        change between costumes."""
        flags = (0x200 if self.pal_varies else 0) | (0x001 if self.tex_varies else 0)
        cc = self.costume_colors or []
        tracks = [k for k in COLOR_TRACKS if any(k in x for x in cc) and
                  len({x.get(k) for x in cc[:costumes]}) > 1]
        cflags = sum(1 << COLOR_TRACKS.index(k) for k in tracks)
        if not (flags or cflags) or costumes < 2:
            return None
        out = b''
        for c in range(costumes):
            last = c == costumes - 1
            pay = 0 if c == 0 else 1
            if cflags:                      # colors: SetExtValAfter (time moves with the val event, if any)
                op = (19 if last else (18 if not flags else 19))
                out += struct.pack('>I', (op << 25) | (cflags << 15) | pay)
                for k in tracks:
                    out += struct.pack('>I', int(cc[min(c, len(cc) - 1)][k], 16))
            if flags:
                ti, pi = self.costume_map[c]
                op = 11 if last else 10                      # SetValAfter (last) / SetValAfterBlock
                out += struct.pack('>I', (op << 25) | (flags << 15) | pay)
                if flags & 0x001:
                    out += struct.pack('>f', float(ti))
                if flags & 0x200:
                    out += struct.pack('>f', float(pi))
        return out + struct.pack('>I', (2 << 25) | 0x5F) + struct.pack('>I', 0)


COLOR_TRACKS = ('prim', 'env', 'blend', 'light1', 'light2')
COLOR_FIELDS = {'prim': 0x50, 'env': 0x58, 'blend': 0x5C, 'light1': 0x60, 'light2': 0x64}   # in MObjSub
