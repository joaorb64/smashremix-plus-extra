"""
Generate placeholder data-screen textures (name, bio, works, specials) for
characters that don't ship hand-drawn PNGs in their datascreen/ folder.
"""
from PIL import Image, ImageDraw, ImageFont

from smashremix_extra.character.bitmap_font import BitmapFont, wrap_bitmap_text


# Name layout based on the vanilla data screen names
NAME_CAP_HEIGHT = 40
NAME_H_SCALE = 0.45
NAME_MAX_HEIGHT = 79
NAME_CENTER_X = 66
# Name container bounds (the 2 line container is wider)
NAME_LEFT = 20
NAME_RIGHT = 106
NAME_RIGHT_2_LINES = 115
NAME_Y = 50
NAME_Y_2_LINES = 48


def _render_name_line(text, font, cap, max_width):
    top = font.getbbox("H")[1]
    bbox = font.getbbox(text)
    scratch = Image.new("L", (bbox[2] + 8, bbox[3] + cap), 0)
    ImageDraw.Draw(scratch).text((4, -top), text, font=font, fill=255)
    ink = scratch.getbbox()
    line = scratch.crop((ink[0], 0, ink[2], max(cap, ink[3])))
    # Squeeze horizontally, and more if the line is too wide
    width = min(max_width, max(1, round(line.width * NAME_H_SCALE)))
    return line.resize((width, line.height), Image.LANCZOS)


def _render_name(lines, font_path, cap, max_width):
    # Size the font so capitals are `cap` pixels tall
    h_bbox = ImageFont.truetype(font_path, 100).getbbox("H")
    font = ImageFont.truetype(font_path, round(
        cap * 100 / (h_bbox[3] - h_bbox[1])))
    rendered = [_render_name_line(l, font, cap, max_width) for l in lines]

    pitch = round((NAME_MAX_HEIGHT - NAME_CAP_HEIGHT) * cap / NAME_CAP_HEIGHT)
    text_w = max(im.width for im in rendered)
    height = max(im.height + i * pitch for i, im in enumerate(rendered))

    # I4 rows must be multiples of 8 bytes
    canvas = Image.new("L", (-(-text_w // 16) * 16, height), 0)
    for i, im in enumerate(rendered):
        canvas.paste(im, ((text_w - im.width) // 2, i * pitch), im)
    return canvas, text_w


def generate_name_image(name, font_path, output_file, scale_x=1.0):
    """Returns the name's (x, y) on the data screen and its line count."""
    lines = name if isinstance(name, list) else [name]
    right = NAME_RIGHT if len(lines) == 1 else NAME_RIGHT_2_LINES
    max_width = right - NAME_LEFT

    canvas, text_w = _render_name(lines, font_path, NAME_CAP_HEIGHT, max_width)
    # Shrink the whole name if it's too tall
    if canvas.height > NAME_MAX_HEIGHT:
        cap = NAME_CAP_HEIGHT * NAME_MAX_HEIGHT // canvas.height
        canvas, text_w = _render_name(lines, font_path, cap, max_width)

    x = round(NAME_CENTER_X - text_w / 2)
    x = max(NAME_LEFT, min(x, right - text_w))
    y = NAME_Y if len(lines) == 1 else NAME_Y_2_LINES

    # Optional extra squeeze, keeping the name's left edge in place
    if scale_x != 1.0:
        text = canvas.crop((0, 0, text_w, canvas.height))
        text = text.resize((max(1, round(text_w * scale_x)), canvas.height),
                           Image.LANCZOS)
        canvas = Image.new("L", (-(-text.width // 16) * 16, canvas.height), 0)
        canvas.paste(text, (0, 0))

    canvas.convert("RGBA").save(output_file)
    return x, y, len(lines)


def generate_bio_image(bio, font_path, output_file,
                       width=160, height=115, start_x=1, start_y=3,
                       line_pitch=10, letter_spacing=1, space_width=1):
    """Lines start at x=start_x, y=start_y + i*line_pitch, matching the
    vanilla bio grid. letter_spacing=1 since these glyphs are cropped with
    no built-in gap."""
    # The in-game bio render crops lines 51 and 102
    # So we generate a 113-row image and duplicate lines 50 and 101
    # to make the final 115-row image
    virtual_height = height - 2
    virtual = Image.new("RGBA", (width, virtual_height), (0, 0, 0, 255))
    font = BitmapFont(
        font_path, letter_spacing=letter_spacing, space_width=space_width)
    lines = wrap_bitmap_text(font, bio, width - 2 * start_x)

    # If the wrapped text overflows the texture height, shrink line
    # spacing (even below glyph height) instead of clipping the end.
    pitch = line_pitch
    if len(lines) > 1:
        max_pitch = (virtual_height - start_y -
                     font.line_height) / (len(lines) - 1)
        pitch = max(1, min(pitch, max_pitch))

    for i, line in enumerate(lines):
        font.draw(virtual, line, start_x, start_y + round(i * pitch))

    image = Image.new("RGBA", (width, height), (0, 0, 0, 255))
    image.paste(virtual.crop((0, 0, width, 51)), (0, 0))
    image.paste(virtual.crop((0, 50, width, 51)), (0, 51))
    image.paste(virtual.crop((0, 51, width, 101)), (0, 52))
    image.paste(virtual.crop((0, 100, width, 101)), (0, 102))
    image.paste(virtual.crop((0, 101, width, 113)), (0, 103))
    image.save(output_file)


def generate_works_image(entries, font_path, output_file,
                         width=160, height=32, slots=3,
                         text_x=4, bullet_x=0, bullet_y0=3, text_y0=0,
                         space_width=1,
                         platform_box_x0=124, platform_box_x1=149):
    """Up to `slots` entries, space-distributed vertically like the vanilla
    works screen. Each entry ({"game", "platform"}) is a bullet + game name
    left-aligned, "(platform)" right-aligned in a fixed box. A game name too
    wide for the font is scaled down to fit; see [[datascreen-image-autogen]]."""
    image = Image.new("RGBA", (width, height), (0, 0, 0, 255))
    draw = ImageDraw.Draw(image)
    font = BitmapFont(font_path, space_width=space_width, letter_spacing=1)

    # Fewer than `slots` entries space out like CSS "space-around": n rows
    # of row_height plus n+1 equal gaps fill the image height.
    used = entries[:slots]
    n = len(used)
    row_height = 10
    row_gap = (height - n * row_height) / (n + 1)
    text_ys = [round(row_gap * (i + 1) + row_height * i) for i in range(n)]
    bullet_dy = bullet_y0 - text_y0

    box_x0 = platform_box_x0
    box_x1 = platform_box_x1
    available_w = box_x0 - text_x - 2

    for i, entry in enumerate(used):
        text_y = text_ys[i]
        bullet_y = text_y + bullet_dy
        draw.rectangle(
            (bullet_x, bullet_y, bullet_x + 1, bullet_y + 1),
            fill=(255, 255, 255, 255))

        draw_y = text_y

        # A game name too wide for the font is rendered at natural size
        # then scaled down to fit (BOX: crisper than bilinear/lanczos,
        # doesn't collapse letters like 'm' the way a bigger NEAREST
        # correction did) - this is actually how vanilla's own
        # "condensed" rows work (not a distinct font).
        game = entry["game"]
        natural_w = font.text_width(game)
        if natural_w > available_w:
            scratch = Image.new(
                "RGBA", (natural_w + text_x, font.line_height),
                (0, 0, 0, 0))
            font.draw(scratch, game, text_x, 0)
            fitted = scratch.resize(
                (text_x + available_w, font.line_height), Image.BOX)
            image.alpha_composite(fitted, (0, draw_y))
        else:
            font.draw(image, game, text_x, draw_y)

        # Draw the platform name in a fixed position
        open_w = font.char_width("(")
        inner_w = box_x1 - box_x0 - open_w
        platform = entry["platform"]
        char_widths = [font.char_width(ch) for ch in platform]
        text_w = sum(char_widths)

        font.draw(image, "(", box_x0, draw_y)
        if text_w <= inner_w:
            # Distribute the gap evenly around and between the letters instead of centering them
            gap = (inner_w - text_w) / (len(platform) + 1)
            cx = box_x0 + open_w + gap
            for ch, cw in zip(platform, char_widths):
                font.draw(image, ch, round(cx), draw_y)
                cx += cw + gap
        else:
            # Too wide even at zero gap - squeeze to fit
            natural_w = font.text_width(platform)
            scratch = Image.new(
                "RGBA", (natural_w, font.line_height), (255, 255, 255, 0))
            font.draw(scratch, platform, 0, 0)
            scratch = scratch.resize(
                (inner_w, font.line_height), Image.BILINEAR)
            image.alpha_composite(scratch, (box_x0 + open_w, draw_y))
        font.draw(image, ")", box_x1, draw_y)

    image.save(output_file)


def generate_special_image(text, font_path, output_file,
                           width=64, height=7, margin=1):
    """Special move name caption - single line, top-left aligned. A name
    too wide to fit is squeezed horizontally instead of clipped."""
    image = Image.new("RGBA", (width, height), (0, 0, 0, 255))
    font = BitmapFont(font_path)
    available_w = width - 2 * margin
    text_w = font.text_width(text)

    if text_w <= available_w:
        font.draw(image, text, margin, 0)
    else:
        scratch = Image.new("RGBA", (text_w, height), (255, 255, 255, 0))
        font.draw(scratch, text, 0, 0)
        scratch = scratch.resize((available_w, height), Image.LANCZOS)
        image.alpha_composite(scratch, (margin, 0))

    image.save(output_file)


def generate_blank_image(output_file, width, height):
    Image.new("RGBA", (width, height), (0, 0, 0, 255)).save(output_file)
