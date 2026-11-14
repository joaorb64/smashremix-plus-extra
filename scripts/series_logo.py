#!/usr/bin/env python3
"""Character series logo tools (see extra_characters_guide.md, SERIES LOGO).

    series_logo.py blend extra_characters/<Name> [--force]
        Make <Name>/series_logo.blend from the template
        (appender/templates/series_logo/series_logo.blend) holding the
        character's current logo (series_logo.bin), or the template's Mario
        logo when there is none. Edit the mesh in Blender; the build reads it.

    series_logo.py render extra_characters/<Name> OUT_DIR
        Write what the build makes from the logo into OUT_DIR:
        series_logo.png (CSS), hud.png (HUD emblem) and series_logo_3d.bin
        (the file 0x23 block).

Blender: $BLENDER, else `blender` on the PATH, else the Flatpak
(org.blender.Blender: it can't see /tmp).
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from smashremix_extra.character import series_logo as S  # noqa: E402


def cmd_blend(a):
    out = os.path.join(a.folder, "series_logo.blend")
    if os.path.exists(out) and not a.force:
        raise SystemExit(f"{out} exists (--force to overwrite)")
    gee = os.path.join(a.folder, "series_logo.bin")
    if os.path.isfile(gee):
        with open(gee, "rb") as f:
            tris = S.mesh_from_gee_bin(f.read())
        print(f"{gee}: {len(tris)} triangles")
    else:
        tris = S.mesh_from_blend(S.TEMPLATE_BLEND,
                                 os.path.join(a.folder, ".series_logo_cache"))
        print("no series_logo.bin: starting from the template logo")
    S.write_blend(tris, out)
    cache = os.path.join(a.folder, ".series_logo_cache")
    if os.path.isdir(cache):
        import shutil
        shutil.rmtree(cache)
    print(f"-> {out}")
    if os.path.isfile(gee):
        print("The build now uses the .blend (the .bin is only a fallback when "
              "Blender can't be found); delete series_logo.bin once you're "
              "happy with it.")


def cmd_render(a):
    os.makedirs(a.out_dir, exist_ok=True)
    tris, source, err = S.load_mesh(a.folder,
                                    os.path.join(a.out_dir, "cache"))
    if err:
        print(f"warning: series_logo.blend unreadable ({err}), used the .bin")
    if tris is None:
        raise SystemExit(f"{a.folder}: no series_logo.blend / series_logo.bin")
    S.render_css(tris).save(os.path.join(a.out_dir, "series_logo.png"))
    S.render_hud(tris).save(os.path.join(a.out_dir, "hud.png"))
    block, _, offs = S.build_logo(tris)
    with open(os.path.join(a.out_dir, "series_logo_3d.bin"), "wb") as f:
        f.write(block)
    print(f"{source}: {offs['triangles']} triangles, {offs['vertices']} "
          f"vertices, 3D block {len(block)} B -> {a.out_dir}")


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("blend", help="make the character's series_logo.blend")
    b.add_argument("folder")
    b.add_argument("--force", action="store_true")
    r = sub.add_parser("render", help="preview the generated logo files")
    r.add_argument("folder")
    r.add_argument("out_dir")
    a = p.parse_args()
    {"blend": cmd_blend, "render": cmd_render}[a.cmd](a)


if __name__ == "__main__":
    main()
