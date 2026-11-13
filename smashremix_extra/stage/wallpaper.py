"""A stage's background image (its wallpaper) in background.bin: read it, or write a new
one from an image.

The header's MPGroundData (at the header's resource offset) points to it: field +0x48
`wallpaper` (decomp mptypes.h), an external pointer into background.bin. It's a libultra
Sprite (+0x04 width / height, +0x28 bitmap count, +0x2C bmheight / bmHreal, +0x30 format /
size, +0x34 Bitmap array) drawn as horizontal strips: Bitmap i (16 bytes: width, width_img,
s, t, buf, actualHeight) holds bmHreal rows starting at row i * bmheight (they overlap by a
row, for filtering). RGBA16 (5551), odd rows stored with their 32-bit words swapped (TMEM).

write() changes only the pixels, in the existing strips: background.bin keeps its layout
and pointers, so nothing else has to follow.
"""
import struct

from PIL import Image

WALLPAPER_FIELD = 0x48            # MPGroundData.wallpaper


def _u16(d, o):
    return struct.unpack_from('>H', d, o)[0]


def _external_target(header, res_head, field):
    """The target offset of the header's external pointer at `field` (its resource chain:
    nodes [u16 next / 4][u16 target / 4], next 0xFFFF ends), or None."""
    o, seen = res_head, set()
    while o not in seen and o + 4 <= len(header):
        seen.add(o)
        if o == field:
            return _u16(header, o + 2) * 4
        nxt = _u16(header, o)
        if nxt == 0xFFFF:
            break
        o = nxt * 4
    return None


def _layout(header, background, ground_offset):
    """-> dict(w, h, step, strips=[(buffer offset, width, width_img, rows)]) or None (no
    RGBA16 wallpaper)."""
    sprite = _external_target(header, ground_offset, ground_offset + WALLPAPER_FIELD)
    d = background
    if sprite is None or sprite + 0x38 > len(d):
        return None
    w, h = struct.unpack_from('>hh', d, sprite + 0x04)
    n = struct.unpack_from('>h', d, sprite + 0x28)[0]
    step, real = struct.unpack_from('>hh', d, sprite + 0x2C)
    fmt, siz = d[sprite + 0x30], d[sprite + 0x31]
    if (fmt, siz) != (0, 2) or w <= 0 or h <= 0 or step <= 0 or n <= 0:
        return None
    bitmaps = _u16(d, sprite + 0x36) * 4                        # (an internal pointer: target / 4)
    strips = []
    for k in range(n):
        b = bitmaps + 16 * k
        bw, bw_img = struct.unpack_from('>hh', d, b)
        rows = struct.unpack_from('>h', d, b + 0x0C)[0] or real
        strips.append((_u16(d, b + 0x0A) * 4, bw, bw_img, rows))
    return dict(w=w, h=h, step=step, strips=strips)


def _swap_words(row):
    for i in range(0, len(row) - 7, 8):
        row[i:i + 8] = row[i + 4:i + 8] + row[i:i + 4]


def read(header, background, ground_offset):
    """The stage's wallpaper as a PIL RGBA image, or None (none, or not RGBA16)."""
    lay = _layout(header, background, ground_offset)
    if lay is None:
        return None
    w, h, step, d = lay['w'], lay['h'], lay['step'], background
    img = Image.new('RGBA', (w, h))
    px = img.load()
    for k, (buf, bw, bw_img, rows) in enumerate(lay['strips']):
        for y in range(min(step, rows)):
            yy = k * step + y
            if yy >= h:
                break
            row = bytearray(d[buf + y * bw_img * 2:buf + (y + 1) * bw_img * 2])
            if y % 2:
                _swap_words(row)
            for x in range(min(bw, w, len(row) // 2)):
                c = struct.unpack_from('>H', row, 2 * x)[0]
                r, g, b = (c >> 11) & 31, (c >> 6) & 31, (c >> 1) & 31
                px[x, yy] = (r << 3 | r >> 2, g << 3 | g >> 2, b << 3 | b >> 2, 255 if c & 1 else 0)
    return img


def write(header, background, ground_offset, image):
    """background.bin (bytes) with its wallpaper replaced by `image` (PIL), scaled to the
    wallpaper's size -> (new bytes, (w, h), resized?). Raises ValueError without an RGBA16
    wallpaper to write into."""
    lay = _layout(header, background, ground_offset)
    if lay is None:
        raise ValueError('background.bin has no RGBA16 wallpaper to replace')
    w, h, step = lay['w'], lay['h'], lay['step']
    img = image.convert('RGBA')
    resized = img.size != (w, h)
    if resized:
        img = img.resize((w, h), Image.LANCZOS)
    px = img.load()
    d = bytearray(background)
    for k, (buf, bw, bw_img, rows) in enumerate(lay['strips']):
        for y in range(rows):                       # every row a strip loads (they overlap)
            yy = min(k * step + y, h - 1)
            row = bytearray(bw_img * 2)
            for x in range(min(bw, bw_img)):
                r, g, b, a = px[min(x, w - 1), yy]
                struct.pack_into('>H', row, 2 * x,
                                 (r >> 3) << 11 | (g >> 3) << 6 | (b >> 3) << 1 | (1 if a >= 128 else 0))
            for x in range(bw, bw_img):             # (padding: the row's last texel)
                row[2 * x:2 * x + 2] = row[2 * (bw - 1):2 * bw]
            if y % 2:
                _swap_words(row)
            o = buf + y * bw_img * 2
            if o + len(row) > len(d):
                raise ValueError(f'background.bin: strip {k} runs past the file')
            d[o:o + len(row)] = row
    return bytes(d), (w, h), resized
