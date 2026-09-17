"""
Generate placeholder data-screen textures (name, bio, works, specials) for
characters that don't ship hand-drawn PNGs in their datascreen/ folder.
"""
from PIL import Image, ImageDraw, ImageFont

from smashremix_extra.character.bitmap_font import BitmapFont, wrap_bitmap_text


def generate_name_image(name, font_path, output_file,
                        width=112, height=40, margin=0, max_width_ratio=0.5):
    image = Image.new("RGBA", (width, height), (0, 0, 0, 255))
    font = ImageFont.truetype(font_path, height)

    # Crop tight to the glyphs' ink bounding box, then stretch that crop to
    # fill the target box.
    scratch = Image.new("RGBA", (width * 4, height * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(scratch)
    bbox = draw.textbbox((0, 0), name, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    draw.text((-bbox[0], -bbox[1]), name, font=font, fill=(255, 255, 255, 255))

    target_w = int((width - 2 * margin) * max_width_ratio)
    if text_w > 0 and text_h > 0:
        glyphs = scratch.crop((0, 0, text_w, text_h))
        glyphs = glyphs.resize((target_w, height - 2 * margin), Image.LANCZOS)
        image.alpha_composite(glyphs, (margin, margin))
    image.save(output_file)


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
                         platform_box_x0=124, platform_box_x1=149,
                         fallback_font_path="appender/extra_resources/fonts/bio"):
    """Up to `slots` entries, space-distributed vertically like the vanilla
    works screen. Each entry ({"game", "platform"}) is a bullet + game name
    left-aligned, "(platform)" right-aligned in a fixed box. A game name
    too wide for the worksfont falls back to the narrower bio font."""
    image = Image.new("RGBA", (width, height), (0, 0, 0, 255))
    draw = ImageDraw.Draw(image)
    font = BitmapFont(font_path, space_width=space_width, letter_spacing=1)
    fallback_font = BitmapFont(
        fallback_font_path, space_width=space_width, letter_spacing=1)

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

        # Fall back to the narrower bio font instead of overlapping the box.
        game_font = font
        if font.text_width(entry["game"]) > available_w:
            game_font = fallback_font
        game_font.draw(image, entry["game"], text_x, draw_y)

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
            scratch = Image.new(
                "RGBA", (text_w, font.line_height), (0, 0, 0, 0))
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
        scratch = Image.new("RGBA", (text_w, height), (0, 0, 0, 0))
        font.draw(scratch, text, 0, 0)
        scratch = scratch.resize((available_w, height), Image.LANCZOS)
        image.alpha_composite(scratch, (margin, 0))

    image.save(output_file)


def generate_blank_image(output_file, width, height):
    Image.new("RGBA", (width, height), (0, 0, 0, 255)).save(output_file)
