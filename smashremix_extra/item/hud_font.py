"""
Training mode HUD item names (the held item display) drawn with the Quartz TTF,
the LCD typeface the vanilla HUD names are made from (extra_resources/fonts/quartz.ttf).

The vanilla sprites are Quartz rendered big and scaled down: a white body, a grey
shadow up-left and a black outline with a soft outer ring. This redoes that at
SS x resolution and box-downsamples it. The constants were fitted against the ROM's
HUD name sprites (MAXIM TOMATO, BEAM SWORD, ATTACK, CLOAKING DEVICE, ...):
same cap height, widths within a few px. M and W are drawn double width as in vanilla.
Quartz's segment gaps are cut wider (SEG_GAP) so they show as dark notches like vanilla's.
"""
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

TTF = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "extra_resources", "fonts", "quartz.ttf"))

SS = 8              # supersampling
SIZE = 14.375       # font px at 1x
X_SCALE = 0.92      # glyphs narrowed
WIDE = {"M": 1.75, "W": 1.75}   # vanilla M/W take two cells
BOLD = 1            # body dilation (SS px)
SHADOW = (-2, -5)   # shadow offset (dy, dx) in SS px
SHADOW_GREY = 0.5
OUTLINE = 8         # black outline radius (SS px)
RING = 3            # soft ring past it (SS px)
RING_ALPHA = 0.425
TOP = 2.9375        # cap top row
PAD = 1.25          # left margin (px)
GAP = 1.75          # px between glyph bodies
SPACE = 4.0         # px for a space
SEG_GAP = 2         # widen the LCD segment gaps (SS px); Quartz's own (~0.3 px) vanish when downsampled
SEG_CLOSE = 5       # closing radius (SS px) that finds those gaps

_font = None


def _get_font():
    global _font
    if _font is None:
        if not os.path.isfile(TTF):
            raise FileNotFoundError(f"training HUD font missing: {TTF} (Quartz TTF)")
        _font = ImageFont.truetype(TTF, SIZE * SS)
    return _font


def _shift(a, dy, dx):
    out = np.zeros_like(a)
    h, w = a.shape
    ys, yd = (slice(0, h - dy), slice(dy, h)) if dy >= 0 else (slice(-dy, h), slice(0, h + dy))
    xs, xd = (slice(0, w - dx), slice(dx, w)) if dx >= 0 else (slice(-dx, w), slice(0, w + dx))
    out[yd, xd] = a[ys, xs]
    return out


def _dilate(a, r):
    out = a.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if (dx or dy) and dx * dx + dy * dy <= r * r + r:
                out |= _shift(a, dy, dx)
    return out


def _erode(a, r):
    return ~_dilate(~a, r)


def _widen_gaps(a):
    """Cut Quartz's thin segment gaps wider so they survive the downsample."""
    if not SEG_GAP:
        return a
    p = SEG_CLOSE + 1
    padded = np.pad(a, p)
    gaps = (_erode(_dilate(padded, SEG_CLOSE), SEG_CLOSE) & ~padded)[p:-p, p:-p]
    return a & ~_dilate(gaps, SEG_GAP)


def _glyph(ch):
    """Glyph body mask at SS x, trimmed to its ink columns; rows from the font's ascent."""
    f = _get_font()
    left, _, right, bottom = f.getbbox(ch)
    im = Image.new("L", (right - left + 4, bottom + 4))
    ImageDraw.Draw(im).text((-left + 2, 0), ch, font=f, fill=255)
    xs = X_SCALE * WIDE.get(ch, 1)
    im = im.resize((max(1, round(im.width * xs)), im.height), Image.BILINEAR)
    a = _widen_gaps(np.array(im) > 127)
    cols = np.nonzero(a.any(0))[0]
    return a[:, cols[0]:cols[-1] + 1] if len(cols) else None


def has_glyph(ch):
    return ch == " " or _get_font().getmask(ch).getbbox() is not None


def render(text, height=16):
    """RGBA image of `text` in the HUD style (no trailing blank columns)."""
    missing = sorted({ch for ch in text if not has_glyph(ch)})
    if missing:
        raise ValueError(f"training HUD name {text!r}: Quartz has no glyph for {''.join(missing)!r}")
    f = _get_font()
    cap_top = f.getbbox("H")[1]
    pieces, x = [], PAD * SS
    for ch in text:
        if ch == " ":
            x += SPACE * SS
            continue
        m = _glyph(ch)
        if m is None:
            continue
        pieces.append((int(x), m))
        x += m.shape[1] + GAP * SS
    H = height * SS
    W = (int(x) + 4 * SS + SS - 1) // SS * SS
    y0 = int(round(TOP * SS)) - cap_top
    base = np.zeros((H, W), bool)
    for px, m in pieces:
        src = m[max(0, -y0):]
        top = max(0, y0)
        rows = min(src.shape[0], H - top)
        base[top:top + rows, px:px + m.shape[1]] |= src[:rows]
    body = _dilate(base, BOLD) if BOLD else base
    shadow = _shift(body, *SHADOW) & ~body
    solid = _dilate(body | shadow, OUTLINE)
    ring = _dilate(solid, RING) & ~solid
    alpha = np.where(solid, 1.0, np.where(ring, RING_ALPHA, 0.0))
    inten = np.where(body, 1.0, np.where(shadow, SHADOW_GREY, 0.0)) * alpha

    def down(a):
        return a.reshape(height, SS, W // SS, SS).mean((1, 3))
    a, i = down(alpha), down(inten)
    v = np.where(a > 0, i / np.maximum(a, 1e-9), 0.0)
    rgba = np.zeros((height, W // SS, 4), np.uint8)
    rgba[..., 0] = rgba[..., 1] = rgba[..., 2] = np.round(v * 255)
    rgba[..., 3] = np.round(a * 255)
    # drop alpha < 1/17 (encodes as 0 in IA8 anyway) before trimming
    rgba[rgba[..., 3] < 8] = 0
    img = Image.fromarray(rgba, "RGBA")
    bbox = img.getchannel("A").getbbox()
    return img.crop((0, 0, bbox[2] if bbox else 1, height))
