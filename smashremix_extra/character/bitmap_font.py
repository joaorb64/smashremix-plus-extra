"""
Load a folder of per-character glyph PNGs (e.g. extra_resources/fonts/bio/,
one file per character named by its Unicode code point - "65.png" for 'A')
and draw wrapped text with them, matching the actual in-game font instead
of a TTF. To add a character, drop a new "<codepoint>.png" into the folder.
"""
import glob
import os

from PIL import Image


def load_glyph_folder(folder):
    """{char: Image} for every "<codepoint>.png" in `folder`, e.g.
    fonts/bio/65.png -> {'A': Image}."""
    glyphs = {}
    for path in glob.glob(os.path.join(folder, "*.png")):
        stem = os.path.splitext(os.path.basename(path))[0]
        if not stem.isdigit():
            continue
        glyphs[chr(int(stem))] = Image.open(path).convert("RGBA")
    return glyphs


class BitmapFont:
    def __init__(self, glyph_folder, letter_spacing=0, space_width=None,
                 scale=1):
        glyphs = load_glyph_folder(glyph_folder)
        if scale != 1:
            glyphs = {
                ch: g.resize(
                    (g.width * scale, g.height * scale), Image.NEAREST)
                for ch, g in glyphs.items()
            }
        self.glyphs = glyphs
        self.letter_spacing = letter_spacing * scale
        self.line_height = max(
            (g.height for g in self.glyphs.values()), default=8 * scale)
        widths = [g.width for g in self.glyphs.values()]
        self.space_width = space_width * scale if space_width else (
            sum(widths) // len(widths) if widths else 4 * scale)

    def char_width(self, ch):
        if ch == " ":
            return self.space_width
        glyph = self.glyphs.get(ch)
        return glyph.width if glyph else 0

    def text_width(self, text):
        if not text:
            return 0
        return sum(self.char_width(ch) + self.letter_spacing
                   for ch in text) - self.letter_spacing

    def draw(self, image, text, x, y):
        cx = x
        for ch in text:
            glyph = None if ch == " " else self.glyphs.get(ch)
            if glyph is not None:
                image.alpha_composite(glyph, (cx, y))
            cx += self.char_width(ch) + self.letter_spacing


def wrap_bitmap_text(font, text, max_width):
    """Greedy word-wrap using a BitmapFont's actual glyph widths."""
    lines = []
    current = []
    for word in text.split(" "):
        trial = current + [word]
        if not current or font.text_width(" ".join(trial)) <= max_width:
            current = trial
        else:
            lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines
