"""HUD emblem + stock icon handling for a character.

Both sprites live in the character (model) file. main.bin reaches them via
FTAttributes.sprites (attr + 0x340) -> FTSprites in main.bin:
    +0x0 Sprite *stock_sprite   (external -> character.bin)
    +0x4 u16  **stock_luts      (internal -> array of external palette ptrs)
    +0x8 Sprite *emblem         (external -> character.bin)

The emblem is an I4 sprite (tinted with the player color by the HUD and
centered by its width), the stock icon is CI4 with one palette per costume.

Features:
- extract(): writes the final hud.png / stock_icon_<n>.png into the build
  character folder, in the same format as the inputs below.
- hud.png in the character folder replaces the emblem.
- stock_icon_0.png .. stock_icon_<N-1>.png replace the stock icon, one per
  costume. They share one CI4 texture, so they must have the same shape and
  only change colors. Missing costumes reuse stock_icon_0.png.
- When stock icons are given, or the character has more costumes than stock
  palettes, the palette array is rebuilt inside character.bin with one entry
  per costume (otherwise extra costumes read garbage palettes).
- fix_transparent_colors(): transparent palette entries (alpha bit off) are
  forced to black (0x0000) and logged.
"""
from __future__ import annotations

import os
import struct

from PIL import Image

SPRITES_FIELD = 0x340
EMBLEM_MAX = 32  # emblem is scaled down to fit this box (vanilla ~27x25)
NULL_LINK = 0xFFFF


# ---------------------------------------------------------------- reloc utils

def _u16(d, o):
    return struct.unpack_from(">H", d, o)[0]


def _put_u16(d, o, v):
    struct.pack_into(">H", d, o, v)


def _chain(d, start):
    """Offsets of a reloc chain (each entry: next/4 in high half)."""
    out = []
    o = start
    while o != NULL_LINK * 4 and o + 4 <= len(d):
        if o in out:
            raise RuntimeError(f"reloc chain loops at 0x{o:X}")
        out.append(o)
        o = _u16(d, o) * 4
    return out


def _target(d, o):
    return _u16(d, o + 2) * 4


def _set_target(d, o, target):
    _put_u16(d, o + 2, target // 4)


def _append_internal(d, chain_start, offsets):
    """Link new pointer slots (already holding their target) to the chain tail."""
    tail = _chain(d, chain_start)[-1]
    for o in offsets:
        _put_u16(d, tail, o // 4)
        tail = o
    _put_u16(d, tail, NULL_LINK)


def _align(d, n=8):
    while len(d) % n:
        d.append(0)


# ---------------------------------------------------------------- texture utils

def _rgba5551(r, g, b, a):
    return ((r >> 3) << 11) | ((g >> 3) << 6) | ((b >> 3) << 1) | (1 if a >= 128 else 0)


def _from5551(c):
    r = (c >> 11) & 0x1F
    g = (c >> 6) & 0x1F
    b = (c >> 1) & 0x1F
    return (r << 3 | r >> 2, g << 3 | g >> 2, b << 3 | b >> 2, 255 if c & 1 else 0)


def _read_sprite(d, o):
    return {
        "w": struct.unpack_from(">h", d, o + 0x04)[0],
        "h": struct.unpack_from(">h", d, o + 0x06)[0],
        "lut": _target(d, o + 0x20),
        "fmt": d[o + 0x30],
        "siz": d[o + 0x31],
        "nbitmaps": struct.unpack_from(">h", d, o + 0x28)[0],
        "bitmap": _target(d, o + 0x34),
    }


def _read_bitmap(d, o):
    w, w_img = struct.unpack_from(">hh", d, o)
    h = struct.unpack_from(">h", d, o + 0x0C)[0]
    return {"w": w, "w_img": w_img, "buf": _target(d, o + 0x08), "h": h}


def _set_size(d, sprite_off, bitmap_off, w, w_img, h):
    struct.pack_into(">hh", d, sprite_off + 0x04, w, h)
    struct.pack_into(">hh", d, sprite_off + 0x2C, h, h)  # bmheight, bmHreal
    struct.pack_into(">hh", d, bitmap_off, w, w_img)
    struct.pack_into(">h", d, bitmap_off + 0x0C, h)


def _pack4(values):
    out = bytearray()
    for i in range(0, len(values), 2):
        hi = values[i] & 0xF
        lo = values[i + 1] & 0xF if i + 1 < len(values) else 0
        out.append(hi << 4 | lo)
    return out


def _swap_odd_rows(buf, w_img, h):
    """N64 sprite textures swap 32-bit words on odd rows (TMEM layout)."""
    buf = bytearray(buf)
    line = w_img // 2
    for y in range(1, h, 2):
        for i in range(y * line, (y + 1) * line - 7, 8):
            buf[i:i + 8] = buf[i + 4:i + 8] + buf[i:i + 4]
    return buf


def _unpack4(buf, w_img, w, h):
    buf = _swap_odd_rows(buf[:w_img * h // 2], w_img, h)
    rows = []
    for y in range(h):
        row = []
        for x in range(w):
            byte = buf[(y * w_img + x) // 2]
            row.append(byte >> 4 if x % 2 == 0 else byte & 0xF)
        rows.append(row)
    return rows


def _write_buffer(d, chain_start, bitmap_off, old_bytes, new_buf, w_img, h):
    """Write a texture buffer in place if it fits, else append and repoint."""
    new_buf = _swap_odd_rows(new_buf, w_img, h)
    old = _target(d, bitmap_off + 0x08)
    if len(new_buf) <= old_bytes:
        d[old:old + len(new_buf)] = new_buf
        return
    _align(d)
    new = len(d)
    d.extend(new_buf)
    _set_target(d, bitmap_off + 0x08, new)


# ---------------------------------------------------------------- layout

class HudIcons:
    """Locates the FTSprites data for one character.

    main: main.bin bytes (original, unpatched)
    reqlist: raw main_reqlist.txt lines (with ${CHARACTER} placeholders)
    """

    def __init__(self, main, main_int_start, main_ext_start, reqlist):
        self.ok = False
        self.reason = ""
        attr = _find_attr(main)
        field = attr + SPRITES_FIELD
        int_chain = _chain(main, main_int_start)
        ext_chain = _chain(main, main_ext_start)
        if field not in int_chain:
            self.reason = "FTAttributes.sprites is not set"
            return
        self.ftsprites = _target(main, field)
        s = self.ftsprites
        self.int_chain, self.ext_chain = int_chain, ext_chain

        entries = [l.strip() for l in reqlist
                   if l.strip() and not l.startswith("END OF")]

        def is_char(off):
            return off in ext_chain and ext_chain.index(off) < len(entries) \
                and entries[ext_chain.index(off)] == "${CHARACTER}"

        if not (is_char(s) and is_char(s + 8)):
            self.reason = "stock/emblem sprites are not in character.bin"
            return
        if (s + 4) not in int_chain:
            self.reason = "stock palette array pointer is not internal"
            return
        if int_chain[0] == s + 4:
            self.reason = "stock palette array pointer heads the reloc chain"
            return
        self.stock_sprite = _target(main, s)
        self.emblem_sprite = _target(main, s + 8)
        luts = _target(main, s + 4)
        # Array entries are external pointers into character.bin.
        self.luts = []
        o = luts
        while o in ext_chain and is_char(o):
            self.luts.append(_target(main, o))
            o += 4
            if o == s:  # array sits right before FTSprites
                break
        if not self.luts:
            self.reason = "no stock palettes found"
            return
        # Reqlist index the new external pointer (FTSprites+4) will take.
        self.new_req_index = ext_chain.index(s) + 1
        self.ok = True


def _find_attr(main):
    return main.rfind(b"\x00\x64\x00\x64") - 228


# ---------------------------------------------------------------- extraction

def extract(icons, char, out_dir, num_costumes, palette_array=None):
    """Write hud.png and stock_icon_<n>.png from character.bin bytes, in the
    same format the character folder takes as input."""
    os.makedirs(out_dir, exist_ok=True)

    sp = _read_sprite(char, icons.emblem_sprite)
    bm = _read_bitmap(char, sp["bitmap"])
    rows = _unpack4(char[bm["buf"]:], bm["w_img"], bm["w"], bm["h"])
    img = Image.new("RGBA", (bm["w"], bm["h"]))
    img.putdata([(255, 255, 255, v * 17) for row in rows for v in row])
    img.save(f"{out_dir}/hud.png")

    sp = _read_sprite(char, icons.stock_sprite)
    bm = _read_bitmap(char, sp["bitmap"])
    rows = _unpack4(char[bm["buf"]:], bm["w_img"], bm["w"], bm["h"])
    luts = palette_array or icons.luts
    w, h = bm["w"], bm["h"]
    for c in range(num_costumes):
        lut = luts[c % len(luts)]
        pal = [_from5551(_u16(char, lut + i * 2)) for i in range(16)]
        img = Image.new("RGBA", (w, h))
        img.putdata([pal[v] for row in rows for v in row])
        img.save(f"{out_dir}/stock_icon_{c}.png")


# ---------------------------------------------------------------- replacement

def replace_emblem(icons, char, char_int_start, png_path):
    img = Image.open(png_path).convert("RGBA")
    if img.width > EMBLEM_MAX or img.height > EMBLEM_MAX:
        scale = EMBLEM_MAX / max(img.width, img.height)
        img = img.resize((max(1, round(img.width * scale)),
                          max(1, round(img.height * scale))), Image.LANCZOS)
    w, h = img.size
    w_img = (w + 15) // 16 * 16
    values = []
    px = img.load()
    for y in range(h):
        for x in range(w_img):
            if x >= w:
                values.append(0)
                continue
            r, g, b, a = px[x, y]
            lum = (r * 299 + g * 587 + b * 114) // 1000
            values.append(lum * a // 255)
    # The HUD tints the emblem by intensity: stretch so the brightest pixel
    # is full white, like the vanilla emblems.
    peak = max(values) or 1
    values = [min(15, round(v * 15 / peak)) for v in values]
    sp = _read_sprite(char, icons.emblem_sprite)
    bm = _read_bitmap(char, sp["bitmap"])
    _write_buffer(char, char_int_start, sp["bitmap"],
                  bm["w_img"] * bm["h"] // 2, _pack4(values), w_img, h)
    _set_size(char, icons.emblem_sprite, sp["bitmap"], w, w_img, h)


def stock_icon_paths(folder, num_costumes):
    """stock_icon_<n>.png per costume; missing ones fall back to costume 0.
    Returns None when stock_icon_0.png doesn't exist."""
    if not os.path.isfile(f"{folder}/stock_icon_0.png"):
        return None
    paths = []
    for c in range(num_costumes):
        p = f"{folder}/stock_icon_{c}.png"
        paths.append(p if os.path.isfile(p) else paths[0])
    return paths


def replace_stock_icon(icons, char, char_int_start, paths):
    """paths: one png per costume (same shape, only colors differ).
    Writes the shared CI4 texture; returns one 16-color palette per costume."""
    imgs = [Image.open(p).convert("RGBA") for p in paths]
    w, h = imgs[0].size
    for p, img in zip(paths, imgs):
        if img.size != (w, h):
            raise RuntimeError(f"{p}: is {img.width}x{img.height}, "
                               f"stock_icon_0.png is {w}x{h}")
    sp = _read_sprite(char, icons.stock_sprite)
    bm = _read_bitmap(char, sp["bitmap"])
    pxs = [img.load() for img in imgs]

    # Each pixel's colors across all costumes form one palette index.
    keys = {}
    indices = []
    for y in range(h):
        for x in range(w):
            colors = tuple(px[x, y] for px in pxs)
            if colors[0][3] < 128:
                indices.append(0)
                continue
            key = tuple(_rgba5551(*c) | 1 for c in colors)
            if key not in keys:
                keys[key] = len(keys) + 1
            indices.append(keys[key])
    if len(keys) > 15:
        raise RuntimeError(
            f"{paths[0]}: {len(keys)} colors, stock icon allows 15 "
            "(plus transparency). A pixel that differs between costumes "
            "counts as its own color.")

    w_img = (w + 15) // 16 * 16
    values = []
    for y in range(h):
        values += indices[y * w:(y + 1) * w] + [0] * (w_img - w)
    _write_buffer(char, char_int_start, sp["bitmap"],
                  bm["w_img"] * bm["h"] // 2, _pack4(values), w_img, h)
    _set_size(char, icons.stock_sprite, sp["bitmap"], w, w_img, h)

    palettes = []
    for c in range(len(paths)):
        pal = [0] * 16
        for key, i in keys.items():
            pal[i] = key[c]
        palettes.append(pal)
    return palettes


def fix_transparent_colors(char, lut_offsets):
    """Set transparent stock palette entries (alpha bit off) to black.
    A colored transparent entry can bleed into edges when the sprite is
    filtered. Returns [(palette offset, index, old value)] for logging."""
    fixed = []
    for lut in sorted(set(lut_offsets)):
        for i in range(16):  # CI4: 16 colors
            c = _u16(char, lut + i * 2)
            if not c & 1 and c != 0:
                _put_u16(char, lut + i * 2, 0)
                fixed.append((lut, i, c))
    return fixed


def build_palette_array(icons, char, char_int_start, num_costumes, palettes=None):
    """Append palettes (if given) and a per-costume pointer array to
    character.bin. Returns (array offset, palette offsets)."""
    if palettes is not None:
        lut_offsets = []
        for pal in palettes:
            _align(char)
            lut_offsets.append(len(char))
            char.extend(struct.pack(">16H", *pal) + bytes(8))
        _set_target(char, icons.stock_sprite + 0x20, lut_offsets[0])
    else:
        lut_offsets = [icons.luts[c % len(icons.luts)]
                       for c in range(num_costumes)]
    # A few spare entries: Remix copies (count + skipped costumes) palettes.
    entries = lut_offsets + [lut_offsets[0]] * 4
    _align(char)
    array = len(char)
    for t in entries:
        char.extend(struct.pack(">HH", NULL_LINK, t // 4))
    _append_internal(char, char_int_start,
                     [array + i * 4 for i in range(len(entries))])
    return array, lut_offsets


def patch_main(icons, main, main_int_start, array_offset):
    """Turn FTSprites+4 into an external pointer to the new array in
    character.bin. Offsets don't move, only reloc links."""
    slot = icons.ftsprites + 4
    # unlink from internal chain
    chain = _chain(main, main_int_start)
    prev = chain[chain.index(slot) - 1]
    _put_u16(main, prev, _u16(main, slot))
    # link into external chain right after the stock sprite pointer
    before = icons.ftsprites
    _put_u16(main, slot, _u16(main, before))
    _put_u16(main, before, slot // 4)
    _set_target(main, slot, array_offset)


def insert_reqlist_entry(icons, reqlist_lines):
    """Insert ${CHARACTER} at the reqlist slot of the new external pointer."""
    out = []
    count = 0
    inserted = False
    for line in reqlist_lines:
        is_entry = line.strip() and not line.startswith("END OF")
        if is_entry and count == icons.new_req_index and not inserted:
            out.append("${CHARACTER}\n")
            inserted = True
        if is_entry:
            count += 1
        out.append(line)
    if not inserted:
        raise RuntimeError("reqlist shorter than main.bin external chain")
    return out
