"""Generate character-select (CSS) and 1P nameplate textures from text.

Both layouts were calibrated against the vanilla textures (files 0x11 and
0x0C). Each glyph is rendered supersampled, stretched horizontally, placed
with a fixed pixel gap, then box-filtered down to the final resolution.

- 1P name (nameplate_singleplayer.png): white on black, 12px tall. Vanilla
  uses DFGothic-EB with proportional spacing, cap top at row 1, baseline
  under row 8, 1px margin on each side.
- CSS nameplate (nameplate.png): 72x16, white text with a dark drop shadow
  extruded 2px down-left, caps 9px tall starting at x=5. The vanilla font
  isn't available; Rodin UB is the closest match.
"""
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

SS = 8  # supersampling factor

DEFAULT_1P_FONT = "appender/templates/DFGothic EB.ttf"
DEFAULT_CSS_FONT = "appender/templates/FOT-Rodin Pro UB.otf"


def _glyph(font, ch, bold=0.0):
    """Return ch as an ink-cropped float mask (horizontally) on a fixed canvas.

    bold thickens strokes by that many final pixels (faux bold).
    """
    size = font.size
    asc, desc = font.getmetrics()
    img = Image.new("L", (size * 2, asc + desc + size), 0)
    stroke = int(round(bold * SS))
    ImageDraw.Draw(img).text((size // 2, size // 4), ch, font=font, fill=255,
                             stroke_width=stroke, stroke_fill=255)
    a = np.asarray(img, dtype=float) / 255
    cols = np.nonzero(a.max(0) > 0.03)[0]
    if len(cols) == 0:
        return np.zeros((a.shape[0], 0))
    return a[:, cols.min():cols.max() + 1]


def _cap_box(font):
    """Top row and height (hi-res px) of a capital H on the glyph canvas."""
    a = _glyph(font, "H")
    rows = np.nonzero(a.max(1) > 0.5)[0]
    return rows.min(), rows.max() + 1 - rows.min()


def _render_line(text, font, stretch, gap, space, bold=0.0):
    """Lay out text on a hi-res strip. Returns (mask, cap_top_row)."""
    pieces = []
    for ch in text:
        if ch == " ":
            pieces.append(np.zeros((1, int(round(space * SS)))))
            continue
        g = _glyph(font, ch, bold)
        w = max(1, int(round(g.shape[1] * stretch)))
        g = np.asarray(Image.fromarray((g * 255).astype(np.uint8)).resize(
            (w, g.shape[0]), Image.BILINEAR), dtype=float) / 255
        pieces.append(g)
    height = max(p.shape[0] for p in pieces)
    gap_px = int(round(gap * SS))
    width = sum(p.shape[1] for p in pieces) + gap_px * max(0, len(pieces) - 1)
    strip = np.zeros((height, max(1, width)))
    x = 0
    for p in pieces:
        if p.shape[0] > 1:
            strip[:p.shape[0], x:x + p.shape[1]] = np.maximum(
                strip[:p.shape[0], x:x + p.shape[1]], p)
        x += p.shape[1] + gap_px
    return strip, _cap_box(font)[0] - int(round(bold * SS))


def _font_for_cap(font_path, cap_px):
    """Load font_path at the size whose capital H is cap_px tall."""
    probe = ImageFont.truetype(font_path, 100 * SS)
    _, h = _cap_box(probe)
    size = int(round(100 * SS * cap_px * SS / h))
    return ImageFont.truetype(font_path, size)


def _place(strip, cap_top, out_w, out_h, x, y):
    """Paste strip so its cap top lands at (x, y) in final px; downsample."""
    canvas = np.zeros((out_h * SS, out_w * SS))
    ox, oy = int(round(x * SS)), int(round(y * SS)) - cap_top
    sy0, dy0 = max(0, -oy), max(0, oy)
    h = min(strip.shape[0] - sy0, canvas.shape[0] - dy0)
    w = min(strip.shape[1], canvas.shape[1] - ox)
    if h > 0 and w > 0:
        canvas[dy0:dy0 + h, ox:ox + w] = strip[sy0:sy0 + h, :w]
    return canvas.reshape(out_h, SS, out_w, SS).mean(axis=(1, 3))


def generate_1p_name_image(text, output_file, font_path=DEFAULT_1P_FONT,
                           cap=8.25, stretch=1.33, gap=0.75, margin=1,
                           max_width=120, contrast=1.15):
    """Render a 1P mode name (e.g. "Pikachu") like the vanilla title cards.

    Names wider than max_width (the widest 1P name textures known to work
    are 120px) are squeezed horizontally to fit.
    The texture width is padded to a multiple of 8 (I8 rows must be whole
    8-byte TMEM lines, otherwise the texture shears); returns the visible
    width (ink + margins) so it can be used as the 1P alt_name_width.
    """
    font = _font_for_cap(font_path, cap)
    strip, cap_top = _render_line(text, font, stretch, gap,
                                  space=cap * 0.3)
    avail = (max_width - 2 * margin) * SS
    if strip.shape[1] > avail:
        strip = np.asarray(Image.fromarray(
            (strip * 255).astype(np.uint8)).resize(
            (avail, strip.shape[0]), Image.BILINEAR), dtype=float) / 255
    ink_w = int(np.ceil(strip.shape[1] / SS))
    visible = ink_w + 2 * margin
    width = (visible + 7) // 8 * 8
    a = _place(strip, cap_top, width, 12, margin, 1)
    # Vanilla names have crisper edges than plain supersampling (fewer gray pixels): sharpen the
    # anti-aliasing a bit. 1.15 matches vanilla's mix of solid and gray pixels, same brightness.
    a = (a - 0.5) * contrast + 0.5
    Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8), "L").save(
        output_file)
    return visible


def generate_css_nameplate(text, output_file, font_path=DEFAULT_CSS_FONT,
                           cap=9.0, stretch=1.0, gap=0.5, bold=0.0, x=5,
                           right=63, width=72, height=16):
    """Render a character select screen nameplate (uppercase, drop shadow).

    Names longer than x..right are squeezed horizontally to fit. right=63
    matches vanilla CAPTAIN FALCON, whose text ends at column 62; going
    further runs past the visible plate on the CSS.
    """
    text = text.upper()
    # Faux bold grows the caps by 2*bold; shrink the font to compensate.
    font = _font_for_cap(font_path, cap - 2 * bold)
    strip, cap_top = _render_line(text, font, stretch, gap, space=cap * 0.4,
                                  bold=bold)
    avail = (min(right, width - 1) - x) * SS
    if strip.shape[1] > avail:
        new_w = avail
        strip = np.asarray(Image.fromarray(
            (strip * 255).astype(np.uint8)).resize(
            (new_w, strip.shape[0]), Image.BILINEAR), dtype=float) / 255
    text_a = _place(strip, cap_top, width, height, x, 1)

    # Shadow: text extruded 2px down-left, slightly blurred, 90% black.
    shadow = np.zeros_like(text_a)
    for i in (1, 2):
        s = np.zeros_like(text_a)
        s[i:, :width - i] = text_a[:height - i, i:]
        shadow = np.maximum(shadow, s)
    shadow = np.asarray(Image.fromarray(
        (shadow * 255).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(0.5)), dtype=float) / 255 * 0.9

    alpha = text_a + (1 - text_a) * shadow
    lum = np.where(alpha > 0, text_a / np.maximum(alpha, 1e-6), 0)
    la = np.stack([np.clip(lum, 0, 1), np.clip(alpha, 0, 1)], -1)
    Image.fromarray((la * 255).astype(np.uint8), "LA").save(output_file)
