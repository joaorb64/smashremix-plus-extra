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
  * costume PNGs saved as paletted (mode P) PNGs, every costume -> exact: the PNG's
                              indices are the texels, its palette (+ tRNS alpha) is that
                              costume's palette. For palette tricks: colors that are the
                              same in one costume and different in another.
Non-CI formats (IA4/8/16, I4/8, RGBA16) are re-encoded from the PNG.
Materials that aren't in the manifest (made in Blender) become CI4 from their
costume images (16 colors).
"""
import os
import re
import struct

import numpy as np
from PIL import Image

import model_files as MF
import matanim as MA

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


def uses_texel_alpha(state):
    """Does a material's own render state make texel alpha matter: a render mode of its own,
    alpha compare, or CVG_X_ALPHA? [(w0, w1)]. Without one, a fighter part draws in the game's
    opaque mode (ftDisplayMainDecideFogDraw: G_RM_AA_ZB_OPA_SURF2), so alpha-0 texels (DK's
    pupils, Ken's eyebrows: 0x0000 palette entries) are drawn solid."""
    for w0, w1 in state:
        if w0 == 0xE200001C or (w0 == 0xE2001E01 and w1 & 3) or (w0 == 0xE2001300 and w1 & 0x1000):
            return True
    return False


def g(w0, w1=0):
    return struct.pack('>II', w0 & 0xFFFFFFFF, w1 & 0xFFFFFFFF)


def rgba5551(c):
    r, gg, b, a = (int(x) for x in c[:4])
    return 0 if a < 128 else ((r >> 3) << 11 | (gg >> 3) << 6 | (b >> 3) << 1 | 1)


def decode_pal(raw):
    return np.array([MF.rgba16(v) for v in struct.unpack(f'>{len(raw) // 2}H', raw)], np.uint8)


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


# Set by the import (import_fighter.Materials) from the export manifest's 'tex_flip_v': its PNGs
# are upside down from the game's row order (upright to edit). Every PNG read is flipped back.
FLIP_V = False


def _rows(a):
    return np.ascontiguousarray(a[::-1]) if FLIP_V else a


class Indexed(np.ndarray):
    """An RGBA image read from a paletted PNG, keeping its indices (.index) and palette (.pal)."""


def load_indexed(path):
    """RGBA array of the PNG; a paletted PNG's also carries .index (h x w) and .pal (n x 4)."""
    pim = Image.open(path)
    rgba = _rows(np.asarray(pim.convert('RGBA')))
    if pim.mode != 'P':
        return rgba
    idx = _rows(np.asarray(pim, np.uint8))
    raw = pim.getpalette('RGB') or []
    n = len(raw) // 3
    alpha = pim.info.get('transparency', b'')
    if isinstance(alpha, int):
        alpha = bytes(255 if k != alpha else 0 for k in range(n))
    pal = np.array([[raw[3 * k], raw[3 * k + 1], raw[3 * k + 2], alpha[k] if k < len(alpha) else 255]
                    for k in range(n)], np.uint8).reshape(-1, 4)
    out = rgba.view(Indexed)
    out.index, out.pal = idx, pal
    return out


def load_png(path):
    if not path or not os.path.exists(path):
        return None
    return _rows(np.asarray(Image.open(path).convert('RGBA')))


def resize(im, w, h):
    im = np.asarray(im, np.uint8)
    if im.shape[:2] == (h, w):
        return im
    return np.asarray(Image.fromarray(im, 'RGBA').resize((w, h), Image.NEAREST))


class Material:
    """One rebuilt material: encoded image / palette lists + how it's drawn."""

    def __init__(self, name, entry, base, root, costume_imgs, props, costumes, cloak_safe=True, layout=1,
                 costume_paths=None, sources=None):
        self.name = name
        self.sources = dict(sources or {})        # costume added in Blender -> the one it copies
        self.cloak_safe = cloak_safe
        self.cutout = False
        e = entry or {}
        self.warnings = []
        self.new = not entry
        # material animation (stages, items; matanim.py): the tracks it animates, its image list
        # ('N64 Frame <i>' nodes in Blender)
        self.animated = set(props.get('n64_animated') or [])
        self.frames = [im for im in (load_png(p) for p in props.get('n64_frames') or []) if im is not None]
        # its palette list ('N64 Palette <i>' nodes: the texture drawn with palette i)
        self.pal_frames = [im for im in (load_png(p) for p in props.get('n64_palettes') or []) if im is not None]
        if not entry and self.frames and len(self.frames) > 1:
            costume_imgs = [self.frames[0]] + list(costume_imgs[1:])
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
        self.tile_w, self.tile_h = e.get('tile_size', (w, h))
        if (w & (w - 1) or h & (h - 1)) and (w * BPP[self.siz] // 8) % 8:
            raise SystemExit(f'{name}: {w}x{h} {fmt_name}: sizes that aren\'t powers of two need rows of a multiple '
                             'of 8 bytes')
        self.stride = MF.row_bytes(w, self.siz)        # rows are stored 64-bit aligned
        nbytes = self.stride * h
        if nbytes > (2048 if self.ci else 4096):
            raise SystemExit(f'{name}: {w}x{h} {fmt_name} does not fit the texture memory')
        wrap = e.get('wrap') or {}
        self.wrap_dict = wrap                          # as exported (costume PNGs: MF.clamp_fill)
        self.masks = (wrap['mask_s'], wrap['mask_t']) if 'mask_s' in wrap and 'mask_t' in wrap else None
        self.wrap_s = props.get('n64_wrap_s') or wrap.get('s', 'clamp')
        self.wrap_t = props.get('n64_wrap_t') or wrap.get('t', 'clamp')
        # tile clamped past the texture's size (vanilla fighters): keep the clamp bit
        self.clamp_bits = tuple(2 if (props.get(f'n64_clamp_{a}') or wrap.get(f'clamp_{a}')) else 0
                                for a in 'st')
        for ax in (self.wrap_s, self.wrap_t):
            if ax not in WRAP_BITS:
                raise SystemExit(f'{name}: wrap mode {ax!r} (use repeat, mirror or clamp)')
        self._init_state(e, props)
        self.flags = e.get('flags', 0x05)
        self.costume_colors = e.get('costume_colors')      # MObj colors per costume (kept)
        if self.costume_colors:
            self.costume_colors = self._extend([dict(x) for x in self.costume_colors], costumes)
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
        if self.ci and costume_imgs and all(getattr(im, 'index', None) is not None and im.index.shape == (h, w)
                                            for im in costume_imgs[:costumes]):
            self._build_indexed(costume_imgs, costumes, n_pal)
            self._finish()
            return
        if self.new:
            if len(self.frames) > 1:
                self._build_frames(costumes, n_pal)
            else:
                self._build_new(costume_imgs, costumes, n_pal)
            if self.ci and len(self.pal_frames) > 1 and self.images and self.palettes:
                # more palettes: recolors of its image (palette 0's drawing)
                ref = unpack(self.unpad(self.images[0]), BPP[self.siz], w * h)
                pal0 = decode_pal(self.palettes[0])
                for p in range(len(self.palettes), len(self.pal_frames)):
                    self.palettes.append(encode_pal(self._palette_from(resize(self.pal_frames[p], w, h), ref, pal0,
                                                                       f'palette {p}')))
            self._finish()
            return

        def png(rel):
            p = os.path.join(root, rel) if rel else None
            if not p or not os.path.exists(p):
                return None
            return resize(_rows(np.asarray(Image.open(p).convert('RGBA'))), w, h)

        self.costume_map = self._extend([list(m) for m in e.get('costume_map') or [[0, 0]]], costumes)
        img_addrs = list(e.get('image_addrs') or [])
        pal_addrs = list(e.get('palette_addrs') or []) if self.ci else []
        img_raw = [bytes(base[a:a + nbytes]) if a is not None else None for a in img_addrs]   # None: empty slot
        pal_raw = [bytes(base[a:a + 2 * n_pal]) for a in pal_addrs]
        # current state: CI -> index arrays + palettes; others -> RGBA arrays
        if self.ci:
            pals = [decode_pal(r) for r in pal_raw]
            imgs = [unpack(self.unpad(r), bpp, w * h) if r is not None else None for r in img_raw]
        else:
            pals = []
            imgs = [self._decode(r) if r is not None else None for r in img_raw]
        self.orig_transparent = self._uses_transparency(img_raw, pal_raw)
        orig_imgs = [x.copy() if x is not None else None for x in imgs]
        orig_pals = [x.copy() for x in pals]
        ref_i, ref_p = self.costume_map[0][0], (self.costume_map[0][1] if self.ci else 0)
        # (one file per image / palette, costumes showing them - tex_layout 2: a file a costume shows
        # is read through that costume below, which also takes a repaint; the passes here only read
        # the files no costume shows: other expressions, unused palettes)
        shown = {rel for rel in (e.get('costumes') or []) if rel} if layout >= 2 else set()
        n_imgs0, n_pals0 = len(imgs), len(pals)
        # more palettes / images as files next to the material's (tex_layout 2: <mat>_pal<p>.png,
        # <mat>_img<i>.png past the list's end, numbered on): added to its lists
        if layout >= 2 and root:
            tdir = os.path.join(root, 'textures')
            while self.ci and os.path.exists(os.path.join(tdir, f'{name}_pal{len(pals)}.png')) and \
                    len(self.pal_frames) <= len(pals):
                im = png(f'textures/{name}_pal{len(pals)}.png')
                pals.append(self._palette_from(im, imgs[ref_i], pals[ref_p], f'palette {len(pals)}'))
                pal_raw.append(b'')
                self.warnings.append(f'palette {len(pals) - 1} added (textures/{name}_pal{len(pals) - 1}.png)')
            while os.path.exists(os.path.join(tdir, f'{name}_img{len(imgs)}.png')) and len(self.frames) <= len(imgs):
                im = png(f'textures/{name}_img{len(imgs)}.png')
                imgs.append(nearest_index(im, pals[ref_p])[0] if self.ci else im)
                img_raw.append(b'')
                self.warnings.append(f'image {len(imgs) - 1} added (textures/{name}_img{len(imgs) - 1}.png)')
        # each costume uses what the file it shows is (tex_layout 2: <mat>.png = costume 0's pair,
        # <mat>_pal<p>.png = palette p on that image, <mat>_img<i>.png = image i with that palette): a
        # costume added in Blender, or one pointed at another palette's file, follows its file
        if layout >= 2:
            for c, path in enumerate((costume_paths or [])[:costumes]):
                b_ = os.path.basename(path or '')
                m_ = re.match(rf'{re.escape(name)}(?:_pal(\d+)|_img(\d+))?\.png$', b_)
                if not m_:
                    continue
                ti_, pi_ = ref_i, ref_p
                if m_.group(1) is not None and self.ci and int(m_.group(1)) < len(pals):
                    pi_ = int(m_.group(1))
                elif m_.group(2) is not None and int(m_.group(2)) < len(imgs):
                    ti_ = int(m_.group(2))
                self.costume_map[c] = [ti_, pi_]

        # palette PNGs (drawn with the reference image)
        for p, rel in enumerate((e.get('palettes') or [])[:len(pals)]):
            im = png(rel) if rel not in shown else None
            if im is not None:
                pals[p] = self._palette_from(im, imgs[ref_i], pals[p], f'palette {p}')
        # image PNGs (drawn with the reference palette)
        for i, rel in enumerate((e.get('images') or [])[:len(imgs)]):
            im = png(rel) if imgs[i] is not None and rel not in shown else None
            if im is not None:
                imgs[i] = self._image_from(im, imgs[i], pals[ref_p] if self.ci else None, f'image {i}')
        # costume images from Blender: override the entries each costume uses
        claimed = set()                     # palettes an earlier costume already uses
        for c, (ti, pi) in enumerate(self.costume_map):
            im = costume_imgs[c] if c < len(costume_imgs) else None
            shared = pi in claimed
            claimed.add(pi)
            if im is None or ti >= n_imgs0 or pi >= max(n_pals0, 1) and self.ci:
                continue                                 # (an entry its file added: read already)
            im = resize(im, w, h)
            # not painted in Blender (still the exported costume image): the entry PNGs decide
            orig = orig_imgs[ti] if not self.ci else orig_pals[pi][orig_imgs[ti]].reshape(h, w, 4)
            # the export fills past a clamped tile's extent with the edge texel (MF.clamp_fill)
            if same_image(orig, im) or \
                    same_image(MF.clamp_fill(orig, self.wrap_dict, (self.tile_w, self.tile_h)), im):
                continue
            if not self.ci:
                imgs[ti] = im
                continue
            cols = self._recolor(im, imgs[ti])
            if cols is not None and shared and \
                    any(not same_image(col[None], pals[pi][k][None]) for k, col in cols.items()):
                # palette pi is an earlier costume's too (e.g. costumes added past the original
                # count repeat the last one's): this costume gets its own (or an identical one)
                new = pals[pi].copy()
                for k, col in cols.items():
                    new[k] = col
                pi = next((p for p, q in enumerate(pals) if same_image(q, new)), None)
                if pi is None:
                    pals.append(new)
                    pal_raw.append(b'')
                    pi = len(pals) - 1
                self.costume_map[c] = [ti, pi]
                claimed.add(pi)
                continue
            if cols is not None:                                       # same drawing, new colors: palette pi
                for k, col in cols.items():
                    if not same_image(col[None], pals[pi][k][None]):
                        pals[pi][k] = col
            else:                                                      # repainted: image ti, palette pi's colors
                new_ix = self._repaint_ci(im, imgs[ti], pals[pi], f'costume {c}')
                # new indices: the costumes before this one get them from their own images
                for c2 in range(c):
                    ti2, pi2 = self.costume_map[c2]
                    im2 = costume_imgs[c2] if c2 < len(costume_imgs) else None
                    if ti2 != ti or pi2 == pi:
                        continue
                    flat2 = clean(resize(im2, w, h)).reshape(-1, 4) if im2 is not None else None
                    for k in self.allocated:
                        sel = new_ix.reshape(-1) == k
                        pals[pi2][k] = flat2[sel][0] if flat2 is not None and sel.any() else pals[pi][k]
                # repainted AND recolored (e.g. texels moved to another color's index, with the
                # colors changed too): once each index has one color, those are palette pi's
                cols = self._recolor(im, new_ix)
                if cols is not None:
                    if self.warnings and self.warnings[-1].startswith(f'costume {c}: colors not in'):
                        self.warnings.pop()
                    for k, col in cols.items():
                        pals[pi][k] = col
                imgs[ti] = new_ix
        # palettes added in Blender (N64 Palette nodes past the original's entries): recolors
        # of the reference image
        for p in range(len(pals), len(self.pal_frames) if self.ci else 0):
            pals.append(self._palette_from(resize(self.pal_frames[p], w, h), imgs[ref_i], pals[ref_p],
                                           f'palette {p}'))
            pal_raw.append(b'')
            self.warnings.append(f'palette {p} added to the list')
        # images added to the list in Blender (N64 Frame nodes past the original's entries)
        for i in range(len(imgs), len(self.frames)):
            im = resize(self.frames[i], w, h)
            imgs.append(nearest_index(im, pals[ref_p])[0] if self.ci else im)
            img_raw.append(b'')
            self.warnings.append(f'image {i} added to the list')
        # the list's order in Blender (alternate textures removed / moved: n64_image_order, the
        # indices of the list above in their new order); the costumes follow their images
        order = props.get('n64_image_order')
        if order is not None:
            known = int(props.get('n64_image_known') or len(imgs))
            order = list(dict.fromkeys([int(x) for x in order if 0 <= int(x) < len(imgs)] +
                                       list(range(known, len(imgs)))))
            kept = sorted({ti for ti, _ in self.costume_map} - set(order))
            if kept:
                self.warnings.append(f'images {kept} are drawn by costumes: kept (at the end of the list)')
                order += kept
            if order != list(range(len(imgs))):
                at = {o: k for k, o in enumerate(order)}
                gone = [i for i in range(len(imgs)) if i not in at]
                imgs, img_raw = [imgs[o] for o in order], [img_raw[o] for o in order]
                self.costume_map = [[at[ti], pi] for ti, pi in self.costume_map]
                self.warnings.append(f'image list reordered: {order}' + (f' (removed {gone})' if gone else '')
                                     + ': moveset events picking its images by number follow the new order')
        # encode (unchanged entries keep their exact bytes)
        if self.ci:
            self.images = [self.pad(pack(ix, bpp)) if ix is not None else None for ix in imgs]
            self.palettes = [r if np.array_equal(decode_pal(r), p) else encode_pal(p) for r, p in zip(pal_raw, pals)]
        else:
            self.images = []
            for r, im in zip(img_raw, imgs):
                if im is None:
                    self.images.append(None)
                    continue
                enc = self.pad(encode_direct(im, self.fmt, self.siz))
                self.images.append(r if r and enc == self.pad(encode_direct(self._decode(r), self.fmt, self.siz))
                                   else enc)
            self.palettes = []
        self._finish()

    def _build_frames(self, costumes, n_pal):
        """A new material with an image list (N64 Frame nodes, for a material animation):
        one image per frame, CI formats sharing one palette."""
        frames = [resize(im, self.w, self.h) for im in self.frames]
        if self.ci:
            ix, pal, reduced = first_seen(np.concatenate(frames, 0), n_pal)
            if reduced:
                self.warnings.append(f'its images have more than {n_pal} colors together, reduced')
            n = self.w * self.h
            self.images = [self.pad(pack(ix[k * n:(k + 1) * n], BPP[self.siz])) for k in range(len(frames))]
            self.palettes = [encode_pal(pal)]
        else:
            self.images = [self.pad(encode_direct(im, self.fmt, self.siz)) for im in frames]
            self.palettes = []
        self.costume_map = [[0, 0]] * costumes
        self.image_source = 'list'
        self.palette_source = 'inline' if self.ci else None
        self.flags = 0x01

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
        return MF.decode_texture(raw + b'\0' * 8, 0, self.fmt, self.siz, self.w, self.h, None)

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
        geom = MF.GEOM_DEFAULT if old_geom is None else old_geom & MF.GEOM_BITS
        if cull is not None:
            geom = (geom & ~0x600) | CULL[cull]
        if lighting is not None:
            geom = (geom & ~MF.G_LIGHTING) | (MF.G_LIGHTING if lighting == 'on' else 0)
        self.state = [s for s in self.state if s[0] >> 24 != 0xD9] + [(MF.GEOM_CMD, geom)]
        self.cull = next(k for k, v in CULL.items() if v == geom & 0x600)
        self.lit = bool(geom & MF.G_LIGHTING)
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
        cols = self._extend(cols, costumes)
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

    def _extend(self, per_costume, costumes):
        """A per-costume list to `costumes` entries: a costume added in Blender takes the one it copies
        (self.sources), else (older .blends) the last; past the count: cut."""
        import copy
        out = list(per_costume)
        while len(out) < costumes:
            c = len(out)
            src = self.sources.get(c, len(out) - 1)
            out.append(copy.deepcopy(out[min(src, len(out) - 1)]))
        return out[:costumes]

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

    def _build_indexed(self, costume_imgs, costumes, n_pal):
        """Every costume a paletted PNG: its indices and palette as they are (deduplicated)."""
        bpp = BPP[self.siz]
        if self.siz == 1:                    # CI8: the TLUT load reads the colors the PNGs use (x16)
            used = max(int(im.index.max()) + 1 for im in costume_imgs[:costumes])
            n_pal = self.n_pal = min(256, -(-used // 16) * 16)
        img_at, pal_at = {}, {}
        self.images, self.palettes, self.costume_map = [], [], []
        for c in range(costumes):
            im = costume_imgs[min(c, len(costume_imgs) - 1)]
            if im.index.max() >= n_pal:
                raise SystemExit(f'{self.name}: costume {c}: index {im.index.max()} past the {n_pal} colors')
            pal = np.zeros((n_pal, 4), np.uint8)
            pal[:min(n_pal, len(im.pal))] = im.pal[:n_pal]
            data, pdata = self.pad(pack(im.index.ravel(), bpp)), encode_pal(pal)
            ti = img_at.setdefault(data, len(img_at))
            if ti == len(self.images):
                self.images.append(data)
            pi = pal_at.setdefault(pdata, len(pal_at))
            if pi == len(self.palettes):
                self.palettes.append(pdata)
            self.costume_map.append([ti, pi])
        self.image_source = 'list' if len(self.images) > 1 else 'inline'
        self.palette_source = 'list' if len(self.palettes) > 1 else 'inline'
        self.flags = (0x01 if self.image_source == 'list' else 0) | (0x04 if self.palette_source == 'list' else 0)

    def _finish(self):
        tex_ids = {m[0] for m in self.costume_map}
        pal_ids = {m[1] for m in self.costume_map}
        self.tex_varies = len(tex_ids) > 1
        self.pal_varies = self.ci and len(pal_ids) > 1
        n_images = len([x for x in (self.images or []) if x is not None])
        if self.tex_varies or 'tex' in self.animated or n_images > 1:  # per-costume / animated / alternate
            self.image_source = 'list'                                 # images need the MObj's list
        if self.siz == 1 and self.ci and self.image_source == 'list' and 'tex' not in self.animated:
            # the MObj's own TLUT load is 16 colors when its image SETTIMG is 16b (needed by the load)
            if self.tex_varies:
                raise SystemExit(f'{self.name}: CI8 images can\'t change per costume (only palettes); use CI4')
            if n_images > 1:
                self.warnings.append('CI8: its image list (alternate textures) can\'t be used; use CI4')
            self.image_source = 'inline'
        if self.pal_varies or (self.ci and 'pal' in self.animated):
            self.palette_source = 'list'
        self.transparent = self._uses_transparency(self.images, self.palettes)
        if self.transparent and self.cloak_safe and self.orig_transparent and not uses_texel_alpha(self.state):
            # alpha-0 texels the game already drew solid (fighter opaque mode, no setup of the
            # material's own; older exports wrote them as transparent PNG pixels): keep them solid,
            # don't turn them into a cut-out
            self.transparent = False
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
        self.allocated = []
        if same_image(pal[old].reshape(im.shape), im):
            return old                                                  # unedited
        if self.cloak_safe and not uses_texel_alpha(self.state):
            # drawn solid whatever the alpha (fighter opaque mode): match colors only, so an
            # opaque painted black is the alpha-0 black entry (DK's pupils)
            im = np.asarray(im).copy()
            im[..., 3] = 255
            pal_cmp = np.asarray(pal).copy()
            pal_cmp[:, 3] = 255
        else:
            pal_cmp = pal
        # colors the palette doesn't have: into indices the image doesn't use (e.g. a duplicate
        # entry), most used first, instead of the nearest color (which would merge them)
        flat = clean(im).reshape(-1, 4)
        pp = clean(pal_cmp)
        far = np.abs(flat[:, None, :] - pp[None, :, :]).max(-1).min(1) > TOL
        free = [k for k in range(len(pal)) if k not in set(np.unique(old).tolist())]
        if far.any() and free:
            cols, counts = np.unique(flat[far], axis=0, return_counts=True)
            for col in cols[np.argsort(-counts)][:len(free)]:
                k = free.pop(0)
                pal[k] = col
                if pal_cmp is not pal:
                    pal_cmp[k] = col
                self.allocated.append(k)
        ix, err = nearest_index(im, pal_cmp)
        if err > 24:
            self.warnings.append(f'{what}: colors not in the palette were mapped to the nearest one '
                                 '(edit the palette PNGs to change colors)')
        return ix

    def _repaint_ci(self, im, old, pal, what):
        """A costume image that isn't a pure recolor of the CI image: texels keep their index
        unless their color differs from the rest of that index's texels (in this image); those
        go to the index whose texels have their color, else an index the image doesn't use
        (palette set to it), else the nearest palette color. Keeps the drawing's index layout
        even when every color changed too (a recolor + a few repainted texels)."""
        self.allocated = []
        flat = clean(im).reshape(-1, 4)
        if self.cloak_safe and not uses_texel_alpha(self.state):
            flat = flat.copy()
            flat[:, 3] = 255                    # (drawn solid: alpha doesn't tell colors apart)
        ix = np.asarray(old).reshape(-1).copy()
        color_of, moved = {}, np.zeros(len(ix), bool)
        for k in np.unique(ix):
            sel = np.nonzero(ix == k)[0]
            cols, counts = np.unique(flat[sel], axis=0, return_counts=True)
            main = cols[counts.argmax()]
            color_of[int(k)] = main
            moved[sel[np.abs(flat[sel] - main).max(1) > TOL]] = True
        free = [k for k in range(len(pal)) if k not in color_of]
        far = False
        cols, counts = np.unique(flat[moved], axis=0, return_counts=True) if moved.any() else ([], [])
        for col in [cols[i] for i in np.argsort(-np.asarray(counts))]:      # most texels first
            sel = moved & (np.abs(flat - col).max(1) <= TOL)
            k = next((k for k, c in color_of.items() if np.abs(c - col).max() <= TOL), None)
            if k is None and free:
                k = free.pop(0)
                color_of[k] = col
                pal[k] = col
                self.allocated.append(k)
            if k is None:
                k, err = nearest_index(col[None], pal)
                k = int(k[0])
                far = far or err > 24
            ix[sel] = k
        if far:
            self.warnings.append(f'{what}: colors not in the palette were mapped to the nearest one '
                                 '(edit the palette PNGs to change colors)')
        return ix.reshape(np.asarray(old).shape)

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
        if self.animated:                       # a material animation plays on the MObj
            return True
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
        for track in self.animated:              # what the animated tracks need to show
            f |= MA.FLAG_OF[track] if track not in ('tex', 'pal') else 0
        return f

    def block(self, mobj_index):
        """DL commands that set this material up -> (bytes, [(offset, 'image'|'palette')]);
        inline image / palette loads need their pointer (at offset + 4) filled in."""
        if self.untextured:                      # texture off; its colors: the MObj's, or prim in the state
            return g(0xD7000000, 0) + (g(0xDE000000, 0x0E000000 + 8 * mobj_index) if self.uses_mobj() else b''), []
        w, h, fmt, siz = self.w, self.h, self.fmt, self.siz
        cms, cmt = WRAP_BITS[self.wrap_s] | self.clamp_bits[0], WRAP_BITS[self.wrap_t] | self.clamp_bits[1]
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
        out.append(g(0xF2000000, ((self.tile_w - 1) << 2) << 12 | ((self.tile_h - 1) << 2)))
        def tlut():
            if self.palette_source != 'list':
                fix.append((len(out) * 8, 'palette'))
                out.append(g(0xFD100000, 0))                         # palette (RGBA16)
            out.append(g(0xE8000000))
            out.append(g(0xF5000100, 0x05000000))
            out.append(g(0xE6000000))
            out.append(g(0xF0000000, 0x05000000 | ((self.n_pal - 1) << 14)))
            out.append(g(0xE7000000))
        # the MObj's DL (objdisplay.c) sets the palette image (flag 0x4; it loads the TLUT
        # only together with its image, flag 0x1) and leaves the IMAGE's SETTIMG last for
        # our LOADBLOCK. So an inline palette with a list image (GoldEditor models) is
        # loaded before the MObj call - after it, its SETTIMG would be what LOADBLOCK
        # reads; a list palette with an inline image (the game's eye materials) after it.
        need_tlut = self.ci and (self.palette_source != 'list' or self.image_source != 'list')
        mobj_image_first = self.uses_mobj() and self.image_source == 'list'
        if need_tlut and mobj_image_first:
            tlut()
        if self.uses_mobj():
            out.append(g(0xDE000000, 0x0E000000 + 8 * mobj_index))   # MObj: its palette / image list entry
        if need_tlut and not mobj_image_first:
            tlut()
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
