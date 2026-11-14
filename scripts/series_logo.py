#!/usr/bin/env python3
"""Character series logo tools (see extra_characters_guide.md, SERIES LOGO).

    series_logo.py convert extra_characters/<Name> [--force]
        Turn the folder's GEE series_logo.bin into series_logo.obj (and remove
        the .bin with --remove-bin).

    series_logo.py new extra_characters/<Name> [--force]
        Start a series_logo.obj from the template (the vanilla Mario logo).

    series_logo.py render extra_characters/<Name> OUT_DIR
        Write what the build makes from the logo into OUT_DIR:
        series_logo.png (CSS), hud.png (HUD emblem) and series_logo_3d.bin
        (the file 0x23 block).

The logo can be edited in any 3D tool (Blender: File > Import > Wavefront /
glTF, then export back to series_logo.obj, .gltf or .glb).
"""
import argparse
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from smashremix_extra.character import series_logo as S  # noqa: E402


def _out(folder, force):
    out = os.path.join(folder, "series_logo.obj")
    if os.path.exists(out) and not force:
        raise SystemExit(f"{out} exists (--force to overwrite)")
    return out


def cmd_convert(a):
    gee = os.path.join(a.folder, "series_logo.bin")
    if not os.path.isfile(gee):
        raise SystemExit(f"{gee}: not found")
    out = _out(a.folder, a.force)
    with open(gee, "rb") as f:
        tris = S.mesh_from_gee_bin(f.read())
    S.write_obj(tris, out)
    print(f"{gee}: {len(tris)} triangles -> {out}")
    if a.remove_bin:
        os.remove(gee)
        print(f"removed {gee}")


def cmd_new(a):
    out = _out(a.folder, a.force)
    shutil.copyfile(S.TEMPLATE_OBJ, out)
    print(f"-> {out}")


def cmd_render(a):
    os.makedirs(a.out_dir, exist_ok=True)
    tris, source = S.load_mesh(a.folder)
    if tris is None:
        raise SystemExit(f"{a.folder}: no series logo "
                         f"({', '.join(S.SOURCE_NAMES)} or series_logo.bin)")
    S.render_css(tris).save(os.path.join(a.out_dir, "series_logo.png"))
    S.render_hud(tris).save(os.path.join(a.out_dir, "hud.png"))
    block, _, offs = S.build_logo(tris)
    with open(os.path.join(a.out_dir, "series_logo_3d.bin"), "wb") as f:
        f.write(block)
    print(f"{source}: {offs['triangles']} triangles, {offs['vertices']} "
          f"vertices, 3D block {len(block)} B -> {a.out_dir}")


def main():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("convert", help="GEE series_logo.bin -> series_logo.obj")
    c.add_argument("folder")
    c.add_argument("--force", action="store_true")
    c.add_argument("--remove-bin", action="store_true")
    n = sub.add_parser("new", help="series_logo.obj from the template")
    n.add_argument("folder")
    n.add_argument("--force", action="store_true")
    r = sub.add_parser("render", help="preview the generated logo files")
    r.add_argument("folder")
    r.add_argument("out_dir")
    a = p.parse_args()
    {"convert": cmd_convert, "new": cmd_new, "render": cmd_render}[a.cmd](a)


if __name__ == "__main__":
    main()
