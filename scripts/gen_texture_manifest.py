#!/usr/bin/env python3
"""
gen_texture_manifest.py - rebuilds textures.txt/specialtextures.txt for a
GE-exported character texture folder, from whatever's currently on disk.

Reads LatestTexturesMulti.txt (one line per texture: p1 p2 size1 size2
flag filename). size1 != 0x40 gets appended as `:xx` to the name.

For specialtextures.txt: uses SpecialTextureMapping.txt verbatim if GE
wrote one (it's authoritative). Otherwise falls back to grouping textures
with their _0..._4 alt files as "Special Palette" blocks - can't tell
those apart from "Special Image" groups without the real mapping file.

Usage:
    python3 scripts/gen_texture_manifest.py /path/to/exported/textures
    python3 scripts/gen_texture_manifest.py   # defaults to cwd
"""
import argparse
import os
import re

ALT_PATTERN = re.compile(r'^([0-9A-Fa-f]{8})\.bmp$')
DEFAULT_SIZE1 = '0040'


def load_manifest_order(manifest_path):
    """Return [(filename, size1), ...] from LatestTexturesMulti.txt, in order."""
    order = []
    with open(manifest_path, 'r', encoding='utf-8') as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) < 6:
                continue
            order.append((parts[5], parts[2]))
    return order


def labeled_name(base, size1):
    """Append `:xx` for a non-default size1 code, e.g. 0000F878.bmp:68."""
    if size1 == DEFAULT_SIZE1:
        return base
    return f"{base}:{size1.lstrip('0') or '0'}"


def find_alts(directory, base_name):
    """Return the existing <addr>_0.bmp .. <addr>_4.bmp variants for a base
    texture, in order, or [] if it has none."""
    m = ALT_PATTERN.match(base_name)
    if not m:
        return []
    addr = m.group(1)
    alts = []
    for n in range(6):
        candidate = f"{addr}_{n}.bmp"
        if os.path.isfile(os.path.join(directory, candidate)):
            alts.append(candidate)
    return alts


def referenced_material_addrs(fbx_path):
    """Returns the set of texture addresses an FBX actually uses, from its
    Material:: connect lines. Used by --scope to filter down to just what
    one exported model needs."""
    with open(fbx_path, 'r', encoding='utf-8', errors='surrogateescape') as f:
        text = f.read()
    return {m.upper() for m in re.findall(
        r'Connect: "OO", "Material::([0-9A-Fa-f]{8})[^"]*", "Model::[^"]+"', text)}


def filter_to_scope(textures_lines, special_lines, addrs):
    """Keeps only lines whose base address (ignoring :xx / _N suffixes)
    is in `addrs`."""
    def base_addr(name):
        stem = name.split(':')[0]
        if stem.lower().endswith('.bmp'):
            stem = stem[:-4]
        stem = re.sub(r'_\d+$', '', stem)
        return stem.upper()

    new_textures = [ln for ln in textures_lines if base_addr(ln) in addrs]

    new_special, keep_block = [], False
    for ln in special_lines:
        m = re.match(r'(Special (?:Image|Palette)): (.+)', ln)
        if m:
            keep_block = base_addr(m.group(2)) in addrs
        if keep_block:
            new_special.append(ln)
    return new_textures, new_special


def build_manifest(directory):
    manifest_path = os.path.join(directory, 'LatestTexturesMulti.txt')
    if not os.path.isfile(manifest_path):
        raise FileNotFoundError(
            f"No LatestTexturesMulti.txt in {directory} - export textures "
            "from GE first.")

    order = load_manifest_order(manifest_path)
    textures_lines = [labeled_name(base, size1) for base, size1 in order]

    special_map_path = os.path.join(directory, 'SpecialTextureMapping.txt')
    if os.path.isfile(special_map_path):
        with open(special_map_path, 'r', encoding='utf-8') as f:
            special_lines = [ln.rstrip('\n') for ln in f]
        while special_lines and special_lines[-1] == "":
            special_lines.pop()
        return textures_lines, special_lines, True

    special_lines = []
    for base, size1 in order:
        name = labeled_name(base, size1)
        alts = find_alts(directory, base)
        if alts:
            special_lines.append(f"Special Palette: {name}")
            for alt in alts:
                special_lines.append(f"Alt: {alt}")
            special_lines.append("")

    return textures_lines, special_lines, False


def main():
    parser = argparse.ArgumentParser(
        description="Regenerate textures.txt/specialtextures.txt for a "
        "GE-exported character texture folder from its LatestTexturesMulti.txt.")
    parser.add_argument('directory', nargs='?', default='.',
                        help="Folder containing LatestTexturesMulti.txt and "
                        "the exported .bmp files (default: current directory)")
    parser.add_argument('--scope', metavar='FBX',
                        help="Only include textures this FBX actually uses "
                        "(e.g. a Special Part export). Writes "
                        "<fbx-stem>_textures.txt instead of textures.txt.")
    args = parser.parse_args()

    directory = os.path.abspath(args.directory)
    textures_lines, special_lines, from_ge = build_manifest(directory)

    if args.scope:
        addrs = referenced_material_addrs(args.scope)
        textures_lines, special_lines = filter_to_scope(textures_lines, special_lines, addrs)
        stem = os.path.splitext(os.path.basename(args.scope))[0]
        textures_path = os.path.join(directory, f'{stem}_textures.txt')
        special_path = os.path.join(directory, f'{stem}_specialtextures.txt')
    else:
        textures_path = os.path.join(directory, 'textures.txt')
        special_path = os.path.join(directory, 'specialtextures.txt')

    with open(textures_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(textures_lines + ['']) + '\n')
    with open(special_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(special_lines) + '\n')

    print(f"Wrote {textures_path} ({len(textures_lines)} textures)")
    image_groups = sum(1 for l in special_lines if l.startswith('Special Image'))
    palette_groups = sum(1 for l in special_lines if l.startswith('Special Palette'))
    source = "GE's own SpecialTextureMapping.txt" if from_ge else "alt-file heuristic (no Special Image detection)"
    print(f"Wrote {special_path} ({image_groups} Special Image + "
          f"{palette_groups} Special Palette group(s), from {source})")


if __name__ == "__main__":
    main()
