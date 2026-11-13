"""Stage select icon (40 x 30) from the stage's own background image, like the vanilla
stages' icons: the wallpaper sprite background.bin holds (stage/wallpaper.py), scaled down."""
from PIL import Image

from smashremix_extra.stage import wallpaper

ICON_SIZE = (40, 30)


def create_stage_icon(header_path, background_path, ground_offset, output_path):
    """icon.bmp from the stage's wallpaper: cropped to 4:3 around its center, scaled to
    40 x 30. -> True, or False when the stage has no wallpaper to use."""
    img = wallpaper.read(open(header_path, 'rb').read(), open(background_path, 'rb').read(), ground_offset)
    if img is None:
        return False
    w, h = img.size
    tw, th = (w, round(w * 3 / 4)) if w * 3 <= h * 4 else (round(h * 4 / 3), h)
    x0, y0 = (w - tw) // 2, (h - th) // 2
    icon = img.crop((x0, y0, x0 + tw, y0 + th)).resize(ICON_SIZE, Image.LANCZOS).convert('RGB')
    icon.save(output_path)
    return True
