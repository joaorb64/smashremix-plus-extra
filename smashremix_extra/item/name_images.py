"""
Name images for extra items, emitted as in-RAM libultra Sprites (asm data), each drawn
with the commercial font the game used there (extra_resources/fonts/), with layout
constants fitted against the ROM's own item name images:

- item_switch: Item Switch page 2 row, I4, 12 rows (the text where Remix's own
  rows have it). Myriad (fonts/myriad.ttf), faux bold (vanilla is likely Myriad Bold).
- training: Training mode item menu, IA8 16 rows, mixed case, words run together
  like the vanilla names ("MaximTomato"). Myriad body + 1 px black shadow down-left.
  Names wider than the two-line box (86 px) take two lines, as the vanilla
  "MotionSensor / Bomb" (and Remix's Cloaking Device, Super/Poison Mushroom):
  86x17, last word on line 2, each line the menu font squashed to 3/4 height
  (that sprite is the 16-row font at its own width, 12 rows tall), centered,
  line 2 eight rows under line 1.
- training_hud: Training mode held item display, IA8 16 rows, upper case.
  Quartz (fonts/quartz.ttf) in the vanilla LCD style, see hud_font.py.

A PNG can replace any of them (config.yaml name_images:).
"""
import functools
import math
import os

import numpy as np
from PIL import Image

from smashremix_extra.character import gen_nameplate
from smashremix_extra.item import hud_font

FONTS_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "extra_resources", "fonts"))
MYRIAD = os.path.join(FONTS_DIR, "myriad.ttf")

ROW_HEIGHT = 12            # item switch row image
LIST_WIDTH = 112           # Remix's list image width
LEFT_MARGIN = 1            # Remix's rows start 1 px in
TRAINING_HEIGHT = 16
TRAINING_HUD_TINT = (0x6C, 0xFF, 0x6C, 0xFF)   # green, as Remix's own HUD names (file 0x1D)
TRAINING_MAX_WIDTH = 100
TWO_LINE_WIDTH = 86        # vanilla MotionSensor Bomb / Remix's two-line names
TWO_LINE_HEIGHT = 17
TWO_LINE_ROWS = 12         # each line: the 16-row font squashed to 12 rows
TWO_LINE_PITCH = 8
TWO_LINE_PHASE = 0.375     # source rows the squash is shifted by (fit to vanilla)

# Myriad layouts fitted against the vanilla images (gen_nameplate renderer: cap height px,
# horizontal stretch, px gap between glyph inks, space px, faux bold px, x/y of the cap top
# left in final px, contrast on the coverage).
SWITCH_STYLE = dict(cap=7.25, stretch=1.1, gap=0.75, space=2.5, bold=0.3,
                    x=LEFT_MARGIN, y=1.5, contrast=3.15)    # vanilla I4 rows are crisp
MENU_STYLE = dict(cap=9.0, stretch=0.975, gap=1.75, space=3.0, bold=0.1,
                  x=2.0, y=2.75, contrast=1.05)


@functools.lru_cache(maxsize=None)
def _myriad(cap):
    if not os.path.isfile(MYRIAD):
        raise FileNotFoundError(f"item name font missing: {MYRIAD} (Myriad Pro TTF)")
    return gen_nameplate._font_for_cap(MYRIAD, cap)


def _coverage(text, style, height):
    """Text coverage (0..1 numpy array, `height` rows, trimmed after the ink)."""
    font = _myriad(style["cap"])
    missing = sorted({ch for ch in text if ch != " " and not font.getmask(ch).getbbox()})
    if missing:
        raise ValueError(f"item name {text!r}: Myriad has no glyph for {''.join(missing)!r}")
    strip, cap_top = gen_nameplate._render_line(text, font, style["stretch"], style["gap"],
                                                space=style["space"], bold=style["bold"])
    width = int(math.ceil(style["x"] + strip.shape[1] / gen_nameplate.SS)) + 2
    a = gen_nameplate._place(strip, cap_top, width, height, style["x"], style["y"])
    a = np.clip((a - 0.5) * style["contrast"] + 0.5, 0, 1)
    a[a < 1 / 34] = 0                       # rounds to 0 in I4 / IA8 anyway
    cols = np.nonzero(a.max(0) > 0)[0]
    return a[:, :cols[-1] + 1 if len(cols) else 1]


def _white(a):
    """White RGBA image, alpha = coverage."""
    rgba = np.zeros(a.shape + (4,), np.uint8)
    rgba[..., :3] = 255
    rgba[..., 3] = np.round(a * 255)
    return Image.fromarray(rgba, "RGBA")


def _shadowed(body):
    """Menu style: white body (coverage array) over a black copy 1 px down-left."""
    h, w = body.shape
    b = np.zeros((h, w + 1))
    b[:, 1:] = body                          # room for the shadow on the left
    s = np.zeros_like(b)
    s[1:, :-1] = b[:-1, 1:]                  # pixel (x, y) shadows from body (x+1, y-1)
    a = b + s * (1 - b)
    i = np.where(a > 0, b / np.maximum(a, 1e-9), 0)
    rgba = np.zeros((h, w + 1, 4), np.uint8)
    rgba[..., 0] = rgba[..., 1] = rgba[..., 2] = np.round(i * 255)
    rgba[..., 3] = np.round(a * 255)
    img = Image.fromarray(rgba, "RGBA")
    return img.crop((1, 0, w + 1, h)) if not rgba[..., 3][:, 0].any() else img


def _png(png, max_w, max_h, what):
    img = Image.open(png).convert("RGBA")
    if img.width > max_w or img.height > max_h:
        raise ValueError(f"{what} image {png} is {img.size}, max {max_w}x{max_h}")
    return img


def item_switch_image(text, png=None):
    if png:
        return _png(png, LIST_WIDTH, ROW_HEIGHT, "item switch name")
    img = _white(_coverage(text, SWITCH_STYLE, ROW_HEIGHT))
    if img.width > LIST_WIDTH:
        raise ValueError(f"item switch name {text!r} is {img.width} px wide, max {LIST_WIDTH}")
    return img


def _menu_body(text):
    return _coverage(text, MENU_STYLE, TRAINING_HEIGHT)


def _squashed_line(text):
    """One line of a two-line menu name: the menu font's body squashed to TWO_LINE_ROWS
    rows, width kept, then the 1 px black shadow down-left put under it (scaling the
    shadow too would smear it)."""
    big = _menu_body(text)
    w = big.shape[1]
    body = Image.new("L", (w, big.shape[0] + 4), 0)       # 2 blank rows around
    body.paste(Image.fromarray(np.round(big * 255).astype(np.uint8), "L"), (0, 2))
    # sampled 3/8 row lower: closest to the vanilla sprite (bicubic, as the others tried)
    body = body.resize((w, TWO_LINE_ROWS), Image.BICUBIC,
                       box=(0, 2 + TWO_LINE_PHASE, w, 2 + TWO_LINE_PHASE + big.shape[0]))
    return _shadowed(np.asarray(body, float) / 255)


def _lines(text):
    """Line split of a two-line name: given ("A\\nB"), or the last word on line 2."""
    if "\n" in text:
        return [part.strip() for part in text.split("\n", 1)]
    words = text.split()
    return [" ".join(words[:-1]), words[-1]]


def training_image(text, png=None):
    """Training mode item menu name (words joined, as the vanilla names). A line break in
    `text` forces two lines; otherwise names wider than TWO_LINE_WIDTH get them."""
    if png:
        return _png(png, TRAINING_MAX_WIDTH, TWO_LINE_HEIGHT + 2, "training name")
    if "\n" not in text:
        img = _shadowed(_menu_body(text.replace(" ", "")))
        if img.width <= TWO_LINE_WIDTH or len(text.split()) < 2:
            if img.width > TRAINING_MAX_WIDTH:
                raise ValueError(f"training name {text!r} is {img.width} px wide, max {TRAINING_MAX_WIDTH} "
                                 f"(one word: no second line)")
            return img
    lines = [_squashed_line(line.replace(" ", "")) for line in _lines(text)]
    width = max([TWO_LINE_WIDTH] + [ln.width for ln in lines])
    if width > TRAINING_MAX_WIDTH:
        raise ValueError(f"training name {text!r}: a line is {width} px wide, max {TRAINING_MAX_WIDTH}")
    # descenders on line 2 run past the vanilla 17 rows: grow rather than cut them
    # (the faint row under the shadow is cut, as in the vanilla sprite)
    ink = lines[1].getchannel("A").point(lambda a: 255 if a > 0x33 else 0).getbbox()
    height = max(TWO_LINE_HEIGHT, TWO_LINE_PITCH + (ink[3] if ink else 0))
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for k, line in enumerate(lines):
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        layer.paste(line, ((width - line.width + 1) // 2, TWO_LINE_PITCH * k))
        img = Image.alpha_composite(img, layer)
    return img


def training_hud_image(text, png=None):
    """Training mode held item name (upper case)."""
    if png:
        return _png(png, TRAINING_MAX_WIDTH, TRAINING_HEIGHT, "training HUD name")
    return hud_font.render(text.upper(), TRAINING_HEIGHT)


def _swap_odd_rows(rows):
    """Sprite texels: odd rows have their 32-bit words swapped in pairs."""
    out = []
    for y, row in enumerate(rows):
        if y & 1:
            row = b"".join(row[i + 4:i + 8] + row[i:i + 4] for i in range(0, len(row), 8))
        out.append(row)
    return b"".join(out)


def encode_i4(img):
    """-> (texels, width_img). Intensity = alpha (white text)."""
    w, h = img.size
    wi = (w + 15) // 16 * 16
    rows = []
    for y in range(h):
        row = bytearray(wi // 2)
        for x in range(w):
            r, g, b, a = img.getpixel((x, y))
            v = round(a * max(r, g, b) / 255 / 17)
            row[x // 2] |= v << (4 if x % 2 == 0 else 0)
        rows.append(bytes(row))
    return _swap_odd_rows(rows), wi


def encode_ia8(img):
    w, h = img.size
    wi = (w + 7) // 8 * 8
    rows = []
    for y in range(h):
        row = bytearray(wi)
        for x in range(w):
            r, g, b, a = img.getpixel((x, y))
            i = round((0.299 * r + 0.587 * g + 0.114 * b) / 17)
            row[x] = (i << 4) | round(a / 17)
        rows.append(bytes(row))
    return _swap_odd_rows(rows), wi


def sprite_asm(label, img, fmt, rgba=(0xFF, 0xFF, 0xFF, 0xFF)):
    """Bass source for a one-bitmap Sprite named `label` (+ its texels).
    `rgba` = sprite tint (the IA texture is multiplied by it)."""
    if fmt == "i4":
        texels, wi = encode_i4(img)
        bmfmt, bmsiz, attr, ndisplist = 4, 0, 0x0240, 60       # as Remix's item list
    else:
        texels, wi = encode_ia8(img)
        bmfmt, bmsiz, attr, ndisplist = 3, 1, 0x0201, 36       # as vanilla training names
    w, h = img.size
    lines = [f"OS.align(8)", f"{label}_texels:"]
    for i in range(0, len(texels), 16):
        lines.append("db " + ", ".join(f"0x{b:02X}" for b in texels[i:i + 16]))
    lines += [
        "OS.align(4)",
        f"{label}_bitmap:",
        f"dh {w}, {wi}, 0, 0                    // width, width_img, s, t",
        f"dw {label}_texels",
        f"dh {h}, 0                            // actualHeight, LUT",
        f"{label}:",
        f"dh 0, 0, {w}, {h}                    // x, y, width, height",
        "float32 1.0, 1.0                       // scale",
        "dh 0, 0                                // expx, expy",
        f"dh 0x{attr:04X}, 0x1234                // attr, zdepth",
        "db " + ", ".join(f"0x{c:02X}" for c in rgba) + "              // rgba",
        "dh 0, 0                                // startTLUT, nTLUTs",
        "dw 0                                   // LUT",
        "dh 0, 1                                // istart, istep",
        f"dh 1, {ndisplist}                     // nbitmaps, ndisplist",
        f"dh {h}, {h}                           // bmheight, bmHreal",
        f"db {bmfmt}, {bmsiz}                   // bmfmt, bmsiz",
        "dh 0",
        f"dw {label}_bitmap",
        "dw 0, 0                                // rsp_dl, rsp_dl_next",
        "dh 0, 0                                // frac_s, frac_t",
    ]
    return "\n".join(lines)
