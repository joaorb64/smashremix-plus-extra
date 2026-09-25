#!/usr/bin/env python3
"""
apply_material_flags_to_fbx.py - fixes up a GE-exported FBX so re-importing
it into GE keeps properties GE's own export drops.

- Renames Material:: names (e.g. Material::0000DC50) with the right tags:
  Transparent, ClampS/ClampT, MirrorS/MirrorT, CullBoth. Detected from
  character.bin. Hand-renamed materials are left alone.
- Renames Model::RoomXX with LightColor/ShadowColor tags. Uses
  --displaylist (GE's DisplayLists.txt dump) if given, since that's exact;
  otherwise falls back to the FBX's own Material->Model connections, which
  is less reliable.
- If a room actually has more than one light setup, splits it into
  RoomXX_1, RoomXX_2, ... objects instead of guessing.

Only ever writes a new/updated FBX file - never touches character.bin,
main.bin or config.yaml.

Usage:
    python3 scripts/apply_material_flags_to_fbx.py character.bin model.fbx
    python3 scripts/apply_material_flags_to_fbx.py character.bin model.fbx --in-place
    python3 scripts/apply_material_flags_to_fbx.py character.bin model.fbx -o fixed.fbx
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from smashremix_extra.ge_bin import find_chain_head, find_textures, walk_chain  # noqa: E402
from check_texture_flags import (  # noqa: E402
    compute_material_tags, compute_room_light_tags, compute_texture_setup_correlation,
    parse_material_room_map, room_light_tags_from_dump)

MATERIAL_RE = re.compile(r'(Material::)([0-9A-Fa-f]{8})([^"]*)(")')
MODEL_ROOM_RE = re.compile(r'(Model::)(Room[0-9A-Fa-f]{2})([^"]*)(")')
NCL1_RE = re.compile(r'(_ncl1_\d+)$')
# tags we know how to compute. anything else (e.g. "EnvMapping") is a flag
# we don't understand and must keep, not overwrite.
KNOWN_TAG_RE = re.compile(r'CullBoth|Transparent|ClampS|ClampT|MirrorS|MirrorT')


# --------------------------------------------------------- FBX text parsing
def extract_block(text, marker):
    """Returns (start, end) of the {...} block right after marker."""
    start = text.index(marker)
    brace = text.index("{", start)
    depth = 0
    i = brace
    while True:
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return start, i + 1
        i += 1


def parse_fbx_array(block, key, cast=float):
    """Parses a comma-separated number array, handles line-wrapping too."""
    i = block.index(key) + len(key)
    m = re.match(r'([\d,\-\.\s]+)', block[i:])
    return [cast(v.strip()) for v in m.group(1).replace("\n", " ").split(",") if v.strip()]


def parse_polygons(polys_raw):
    """Decodes PolygonVertexIndex (last index of each polygon is negative)
    into [(vertex_idxs, raw_slice_start, raw_slice_end), ...] - the slice
    bounds also work on NormalsIndex/UVIndex, same order."""
    polygons = []
    cur, start_i = [], 0
    for i, x in enumerate(polys_raw):
        if x < 0:
            cur.append(-x - 1)
            polygons.append((cur, start_i, i + 1))
            cur, start_i = [], i + 1
        else:
            cur.append(x)
    return polygons


def fmt_floats(flat):
    return ",".join(f"{v:g}" for v in flat)


def fmt_ints(vals):
    return ",".join(str(v) for v in vals)


def wrap(s, per_line=20, prefix="\t\t\t,"):
    """Line-wraps a comma-joined value string like GE does. Real parsers
    (ufbx) reject one giant unwrapped line, so this matters."""
    items = s.split(",")
    lines = [",".join(items[i:i + per_line]) for i in range(0, len(items), per_line)]
    out = lines[0]
    for ln in lines[1:]:
        out += "\n" + prefix + ln
    return out


def parse_room_clusters(fbx_text, room):
    """Returns [(bone_name, {old_vertex_idx: weight}), ...] - a room's skin
    weights, per bone. Can be more than one bone for a single room."""
    clusters = []
    for sub in re.findall(rf'Connect: "OO", "(SubDeformer::[^"]+)", "Deformer::Skin {re.escape(room)}"',
                          fbx_text):
        bm = re.search(rf'Connect: "OO", "Model::([^"]+)", "{re.escape(sub)}"', fbx_text)
        if not bm:
            continue
        cblock_s, cblock_e = extract_block(fbx_text, f'Deformer: "{sub}"')
        cblock = fbx_text[cblock_s:cblock_e]
        idxs = parse_fbx_array(cblock, "Indexes: ", int)
        wts = parse_fbx_array(cblock, "Weights: ", float)
        clusters.append((bm.group(1), dict(zip(idxs, wts))))
    return clusters


def remove_unused_default_material(fbx_text):
    """Removes Material::DefaultMaterial - GE connects it to every mesh but
    it's never actually used by any polygon. Only removes it from a mesh
    where we checked it's really unused there. Drops the material
    definition itself once nothing points to it anymore.
    Returns (new_text, [mesh_name, ...] it was removed from)."""
    mesh_names = re.findall(r'Model: "Model::([^"]+)", "Mesh"', fbx_text)
    touched = []
    for name in mesh_names:
        mat_conns = re.findall(
            r'Connect: "OO", "Material::([^"]+)", "Model::' + re.escape(name) + r'"', fbx_text)
        if "DefaultMaterial" not in mat_conns:
            continue
        idx = mat_conns.index("DefaultMaterial")
        try:
            s, e = extract_block(fbx_text, 'Model: "Model::' + name + '"')
        except ValueError:
            continue
        block = fbx_text[s:e]
        if "Materials: " not in block:
            continue
        mats = parse_fbx_array(block, "Materials: ", int)
        if idx in mats:
            continue  # genuinely used here - leave this mesh alone

        new_mats = [m - 1 if m > idx else m for m in mats]
        new_block = re.sub(
            r'Materials: [\d,\-\.\s]+(?=\n\t\t\})',
            f"Materials: {wrap(fmt_ints(new_mats))}", block, count=1)
        fbx_text = fbx_text[:s] + new_block + fbx_text[e:]

        fbx_text = re.sub(
            r'\tConnect: "OO", "Material::DefaultMaterial", "Model::' + re.escape(name) + r'"\n',
            "", fbx_text, count=1)
        touched.append(name)

    if touched and not re.search(r'"Material::DefaultMaterial"', fbx_text):
        try:
            s, e = extract_block(fbx_text, 'Material: "Material::DefaultMaterial"')
            fbx_text = fbx_text[:s] + fbx_text[e:]
            m = re.search(r'ObjectType: "Material" \{\n\t\tCount: (\d+)\n', fbx_text)
            if m:
                fbx_text = fbx_text.replace(
                    m.group(0), f'ObjectType: "Material" {{\n\t\tCount: {int(m.group(1)) - 1}\n', 1)
            m = re.search(r'Count: (\d+)\n', fbx_text)
            if m:
                fbx_text = fbx_text.replace(m.group(0), f"Count: {int(m.group(1)) - 1}\n", 1)
        except ValueError:
            pass

    return fbx_text, touched


def rename_meshes_to_room_by_bone(fbx_text):
    """Renames a Mesh (not already RoomXX) to RoomXX, XX = the bone it's
    bound to. Special Part exports come named "DefaultUnassigned" by
    default, so this makes them work with the rest of our RoomXX tooling.
    Only does this when the mesh is bound to exactly one bone, and skips
    it if that RoomXX name is already taken.
    Returns (new_text, [(old_name, new_name), ...])."""
    mesh_names = re.findall(r'Model: "Model::([^"]+)", "Mesh"', fbx_text)
    taken = {m for m in mesh_names if re.fullmatch(r'Room[0-9A-Fa-f]{2}', m)}
    renamed = []
    for name in mesh_names:
        if re.fullmatch(r'Room[0-9A-Fa-f]{2}', name):
            continue
        bones = {b for b, _ in parse_room_clusters(fbx_text, name)}
        if len(bones) != 1:
            continue
        bone_name = next(iter(bones))
        if not bone_name.isdigit():
            continue
        new_name = f"Room{int(bone_name):02X}"
        if new_name in taken:
            continue
        fbx_text = fbx_text.replace(f'Model::{name}"', f'Model::{new_name}"')
        fbx_text = fbx_text.replace(f'Deformer::Skin {name}"', f'Deformer::Skin {new_name}"')
        fbx_text = fbx_text.replace(f'Geometry::{name}"', f'Geometry::{new_name}"')
        taken.add(new_name)
        renamed.append((name, new_name))
    return fbx_text, renamed


def split_mixed_room(fbx_text, room, texture_setups, room_setups):
    """Splits a room with more than one light setup into RoomXX_1,
    RoomXX_2, ... - one object per group of polygons sharing the same
    setup, kept in the original polygon order.

    Each polygon's setup comes from its material (texture_setups, global
    per-texture, since a texture's lighting doesn't depend on the room).
    If a material has no known setup (can happen - GE has an indirection
    we can't see), and the room has exactly one setup nothing else
    claimed, that leftover setup is assigned to it. Otherwise we can't
    tell and give up on this room.

    Each new object keeps the room's full material/texture list as-is
    (simpler, and an unused material on a mesh is harmless). Skin weights
    are split properly per object so posing still works.

    Returns (new_fbx_text, [new_object_name, ...], None, None) on success.
    On failure: (None, None, resolved_setup, reason). resolved_setup is
    set only when every polygon actually agreed on one setup after all -
    then the caller can just tag the room normally instead of skipping it."""
    try:
        s, e = extract_block(fbx_text, f'Model: "Model::{room}"')
    except ValueError:
        return None, None, None, f"no Model::{room} block found"
    block = fbx_text[s:e]

    verts = parse_fbx_array(block, "Vertices: ", float)
    pts = [tuple(verts[i:i + 3]) for i in range(0, len(verts), 3)]
    polys_raw = parse_fbx_array(block, "PolygonVertexIndex: ", int)
    normals_flat = parse_fbx_array(block, "Normals: ", float)
    uv_flat = parse_fbx_array(block, "\n\t\t\tUV: ", float)
    normals_idx = parse_fbx_array(block, "NormalsIndex: ", int)
    uv_idx = parse_fbx_array(block, "UVIndex: ", int)
    mats = parse_fbx_array(block, "Materials: ", int)
    tex_ids = parse_fbx_array(block, "TextureId: ", int)
    polygons = parse_polygons(polys_raw)

    room_mat_names = re.findall(rf'Connect: "OO", "Material::([^"]+)", "Model::{re.escape(room)}"',
                                fbx_text)
    room_tex_names = re.findall(rf'Connect: "OO", "(Texture::[^"]+)", "Model::{re.escape(room)}"',
                                fbx_text)

    # per-polygon setup assignment
    per_poly_setup = []
    claimed = set()
    unresolved = []
    for pi in range(len(polygons)):
        mat_idx = mats[pi]
        mat_name = room_mat_names[mat_idx] if mat_idx < len(room_mat_names) else None
        # material name can have a tag suffix / _ncl1_N, strip it to get
        # the bare address texture_setups is keyed by
        addr_m = re.match(r'([0-9A-Fa-f]{8})', mat_name) if mat_name else None
        addr = addr_m.group(1).upper() if addr_m else None
        setup = texture_setups.get(addr) if addr else None
        per_poly_setup.append(setup)
        if setup is not None:
            claimed.add(setup)
        else:
            unresolved.append(pi)

    if unresolved:
        remaining = [s for s in room_setups if s not in claimed]
        if len(remaining) != 1:
            return None, None, None, (f"{len(unresolved)} polygon(s) have no correlated light "
                                      f"setup and {len(remaining)} of the room's setups remain "
                                      "unclaimed (need exactly 1 to resolve by elimination)")
        for pi in unresolved:
            per_poly_setup[pi] = remaining[0]

    # group into maximal contiguous runs, in polygon order
    runs = []  # [(setup, [poly_idx, ...]), ...]
    for pi, setup in enumerate(per_poly_setup):
        if runs and runs[-1][0] == setup:
            runs[-1][1].append(pi)
        else:
            runs.append((setup, [pi]))
    if len(runs) < 2:
        # every polygon ended up with the same setup - not actually mixed,
        # just report the one setup so caller can tag it normally
        return None, None, (runs[0][0] if runs else None), "only one contiguous run found - nothing to split"

    def build_group(poly_indices):
        vmap, new_verts = {}, []
        new_poly_raw, new_nidx, new_uidx, new_mats, new_tex = [], [], [], [], []
        for pi in poly_indices:
            vidxs, rs, re_ = polygons[pi]
            n = len(vidxs)
            remapped = []
            for v in vidxs:
                if v not in vmap:
                    vmap[v] = len(new_verts)
                    new_verts.append(pts[v])
                remapped.append(vmap[v])
            for k, rv in enumerate(remapped):
                new_poly_raw.append(rv if k < n - 1 else -(rv + 1))
            new_nidx.extend(normals_idx[rs:re_])
            new_uidx.extend(uv_idx[rs:re_])
            new_mats.append(mats[pi])
            new_tex.append(tex_ids[pi])
        return dict(vmap=vmap, verts=new_verts, poly_raw=new_poly_raw,
                    normals_idx=new_nidx, uv_idx=new_uidx, mats=new_mats, tex_ids=new_tex)

    normals_vals_text = fmt_floats(normals_flat)
    uv_vals_text = fmt_floats(uv_flat)
    header_end = block.index("Vertices: ")
    header_template = block[:header_end]

    def render_model(g, name):
        flat_verts = [c for p in g["verts"] for c in p]
        header = header_template.replace(f'Model::{room}"', f'Model::{name}"', 1)
        return (
            header +
            f"Vertices: {wrap(fmt_floats(flat_verts))}\n"
            f"\t\tPolygonVertexIndex: {wrap(fmt_ints(g['poly_raw']))}\n"
            f"\t\tGeometryVersion: 124\n"
            f"\t\tLayerElementNormal: 0 {{\n"
            f"\t\t\tVersion: 101\n\t\t\tName: \"\"\n"
            f"\t\t\tMappingInformationType: \"ByPolygonVertex\"\n"
            f"\t\t\tReferenceInformationType: \"IndexToDirect\"\n"
            f"\t\t\tNormals: {wrap(normals_vals_text)}\n"
            f"\t\t\tNormalsIndex: {wrap(fmt_ints(g['normals_idx']))}\n"
            f"\t\t}}\n"
            f"\t\tLayerElementUV: 0 {{\n"
            f"\t\t\tVersion: 101\n\t\t\tName: \"default\"\n"
            f"\t\t\tMappingInformationType: \"ByPolygonVertex\"\n"
            f"\t\t\tReferenceInformationType: \"IndexToDirect\"\n"
            f"\t\t\tUV: {wrap(uv_vals_text)}\n"
            f"\t\t\tUVIndex: {wrap(fmt_ints(g['uv_idx']))}\n"
            f"\t\t}}\n"
            f"\t\tLayerElementMaterial: 0 {{\n"
            f"\t\t\tVersion: 101\n\t\t\tName: \"\"\n"
            f"\t\t\tMappingInformationType: \"ByPolygon\"\n"
            f"\t\t\tReferenceInformationType: \"IndexToDirect\"\n"
            f"\t\t\tMaterials: {wrap(fmt_ints(g['mats']))}\n"
            f"\t\t}}\n"
            f"\t\tLayerElementTexture: 0 {{\n"
            f"\t\t\tVersion: 101\n\t\t\tName: \"\"\n"
            f"\t\t\tMappingInformationType: \"ByPolygon\"\n"
            f"\t\t\tReferenceInformationType: \"IndexToDirect\"\n"
            f"\t\t\tBlendMode: \"Normal\"\n\t\t\tTextureAlpha: 1\n"
            f"\t\t\tTextureId: {wrap(fmt_ints(g['tex_ids']))}\n"
            f"\t\t}}\n"
            f"\t\tLayer: 0 {{\n\t\t\tVersion: 100\n"
            f"\t\t\tLayerElement:  {{\n\t\t\t\tType: \"LayerElementNormal\"\n\t\t\t\tTypedIndex: 0\n\t\t\t}}\n"
            f"\t\t\tLayerElement:  {{\n\t\t\t\tType: \"LayerElementMaterial\"\n\t\t\t\tTypedIndex: 0\n\t\t\t}}\n"
            f"\t\t\tLayerElement:  {{\n\t\t\t\tType: \"LayerElementTexture\"\n\t\t\t\tTypedIndex: 0\n\t\t\t}}\n"
            f"\t\t\tLayerElement:  {{\n\t\t\t\tType: \"LayerElementUV\"\n\t\t\t\tTypedIndex: 0\n\t\t\t}}\n"
            f"\t\t}}\n"
            f"\t\tNodeAttributeName: \"Geometry::{name}\"\n\t}}\n"
        )

    orig_clusters = parse_room_clusters(fbx_text, room)

    new_objects, new_names, new_deformer_objs, new_connects = [], [], [], []
    for gi, (run_setup, poly_indices) in enumerate(runs, start=1):
        g = build_group(poly_indices)
        light_rgb, shadow_rgb = run_setup
        suffix = (f"LightColor{light_rgb[0]:02X}{light_rgb[1]:02X}{light_rgb[2]:02X}FF"
                 f"ShadowColor{shadow_rgb[0]:02X}{shadow_rgb[1]:02X}{shadow_rgb[2]:02X}FF")
        name = f"{room}_{gi}_{suffix}"
        new_names.append(name)
        new_objects.append(render_model(g, name))

        new_connects.append(f'\tConnect: "OO", "Model::{name}", "Model::Scene"\n')
        for mat_name in room_mat_names:
            new_connects.append(f'\tConnect: "OO", "Material::{mat_name}", "Model::{name}"\n')
        for tex_name in room_tex_names:
            new_connects.append(f'\tConnect: "OO", "{tex_name}", "Model::{name}"\n')

        for bone_name, weights in orig_clusters:
            sub_weights = {g["vmap"][v]: w for v, w in weights.items() if v in g["vmap"]}
            if not sub_weights:
                continue
            deformer_name = f"Deformer::Skin {name}"
            cluster_name = f"SubDeformer::Cluster {name} {bone_name}"
            idxs_str = ",".join(str(i) for i in sorted(sub_weights))
            wts_str = ",".join(f"{sub_weights[i]:g}" for i in sorted(sub_weights))
            new_deformer_objs.append(
                f'\tDeformer: "{deformer_name}", "Skin" {{\n\t\tVersion: 100\n'
                f'\t\tProperties60:  {{\n\t\t}}\n\t\tLink_DeformAcuracy: 50\n\t}}\n'
                f'\tDeformer: "{cluster_name}", "Cluster" {{\n\t\tVersion: 100\n'
                f'\t\tProperties60:  {{\n\t\t\tProperty: "SrcModelReference", "object", ""\n\t\t}}\n'
                f'\t\tUserData: "", ""\n'
                f'\t\tIndexes: {wrap(idxs_str)}\n'
                f'\t\tWeights: {wrap(wts_str)}\n'
                f'\t\tTransform: 1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1\n'
                f'\t\tTransformLink: 1,0,0,0,-0,1,0,0,0,-0,1,0,0,0,0,1\n\t}}\n'
            )
            new_connects.append(f'\tConnect: "OO", "{deformer_name}", "Model::{name}"\n')
            new_connects.append(f'\tConnect: "OO", "{cluster_name}", "{deformer_name}"\n')
            new_connects.append(f'\tConnect: "OO", "Model::{bone_name}", "{cluster_name}"\n')

    new_text = fbx_text[:s] + "".join(new_objects) + fbx_text[e:]

    # remove the old room's own connect lines, it doesn't exist anymore
    for pat in (
        rf'\tConnect: "OO", "Material::[^"]+", "Model::{re.escape(room)}"\n',
        rf'\tConnect: "OO", "Texture::[^"]+", "Model::{re.escape(room)}"\n',
        rf'\tConnect: "OO", "Model::{re.escape(room)}", "Model::Scene"\n',
        rf'\tConnect: "OO", "Deformer::Skin {re.escape(room)}", "Model::{re.escape(room)}"\n',
        rf'\tConnect: "OO", "SubDeformer::[^"]+", "Deformer::Skin {re.escape(room)}"\n',
    ):
        new_text = re.sub(pat, "", new_text)
    # old bone->cluster connects and Deformer/Cluster defs are left as
    # harmless orphans if unused elsewhere

    anchor = new_text.index("Connections:  {\n") + len("Connections:  {\n")
    new_text = new_text[:anchor] + "".join(new_connects) + new_text[anchor:]

    if new_deformer_objs:
        obj_anchor = new_text.index("Objects:  {\n") + len("Objects:  {\n")
        new_text = new_text[:obj_anchor] + "".join(new_deformer_objs) + new_text[obj_anchor:]

    # keep Definitions: counts accurate (not strictly required, but nice)
    added_models = len(new_names) - 1
    added_deformers = len(new_deformer_objs)
    m = re.search(r'Count: (\d+)\n', new_text)
    if m:
        new_text = new_text.replace(m.group(0), f"Count: {int(m.group(1)) + added_models + added_deformers}\n", 1)
    m = re.search(r'ObjectType: "Model" \{\n\t\tCount: (\d+)\n', new_text)
    if m:
        new_text = new_text.replace(m.group(0),
            f'ObjectType: "Model" {{\n\t\tCount: {int(m.group(1)) + added_models}\n', 1)
    m = re.search(r'ObjectType: "Deformer" \{\n\t\tCount: (\d+)\n', new_text)
    if m and added_deformers:
        new_text = new_text.replace(m.group(0),
            f'ObjectType: "Deformer" {{\n\t\tCount: {int(m.group(1)) + added_deformers}\n', 1)

    return new_text, new_names, None, None


def fix_fbx(fbx_text, material_tags, cull_state):
    """Renames each Material:: with its computed tags. CullBoth: if we
    don't know the cull state (None), keep whatever was there before
    instead of guessing. Unknown tags we don't compute (e.g.
    "EnvMapping") are kept too, just moved after the computed ones.
    Returns (new_text, [(addr, old_tag_text, new_tag_text), ...])."""
    by_addr = {f"{off:08X}": tags for off, (_, _, tags) in material_tags.items()}
    cull_by_addr = {f"{off:08X}": state for off, state in cull_state.items()}
    changed = []

    def repl(m):
        prefix, addr, rest, quote = m.groups()
        ncl1 = ""
        m2 = NCL1_RE.search(rest)
        if m2:
            ncl1 = m2.group(1)
            rest = rest[:m2.start()]
        custom = KNOWN_TAG_RE.sub("", rest)
        addr_u = addr.upper()
        tags = list(by_addr.get(addr_u, []))
        state = cull_by_addr.get(addr_u)
        want_cull = "CullBoth" in rest if state is None else state
        if want_cull:
            tags = ["CullBoth"] + tags
        new_rest = "".join(tags) + custom
        if new_rest != rest:
            changed.append((addr_u, rest, new_rest))
        return f"{prefix}{addr_u}{new_rest}{ncl1}{quote}"

    new_text = MATERIAL_RE.sub(repl, fbx_text)
    return new_text, changed


def fix_rooms(fbx_text, room_tags):
    """Renames each Model::RoomXX with its LightColor/ShadowColor tag.
    Rooms not in room_tags are left as-is.
    Returns (new_text, [(room, old_suffix, new_suffix), ...])."""
    changed = []

    def repl(m):
        prefix, room, rest, quote = m.groups()
        if room not in room_tags:
            return m.group(0)
        light_rgb, shadow_rgb = room_tags[room]
        new_rest = (f"LightColor{light_rgb[0]:02X}{light_rgb[1]:02X}{light_rgb[2]:02X}FF"
                   f"ShadowColor{shadow_rgb[0]:02X}{shadow_rgb[1]:02X}{shadow_rgb[2]:02X}FF")
        if new_rest != rest:
            changed.append((room, rest, new_rest))
        return f"{prefix}{room}{new_rest}{quote}"

    new_text = MODEL_ROOM_RE.sub(repl, fbx_text)
    return new_text, changed


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model", help="GE character .bin file (e.g. character.bin)")
    ap.add_argument("fbx", help="Exported FBX file to fix")
    ap.add_argument("--table", type=lambda x: int(x, 0), default=None,
                    help="internal file table offset (auto-detected if omitted)")
    ap.add_argument("--displaylist", help="GE's \"<Name>DisplayLists.txt\" debug "
                    "dump (Export Model Displaylists in GE) - ground truth for "
                    "room light/shadow tags, preferred over the fbx's own Connect "
                    "lines when given")
    ap.add_argument("--in-place", action="store_true",
                    help="overwrite the input FBX instead of writing <name>_fixed.fbx")
    ap.add_argument("-o", "--output", help="explicit output path")
    a = ap.parse_args()

    d = Path(a.model).read_bytes()
    head = a.table if a.table is not None else find_chain_head(d)
    if head is None:
        raise SystemExit("couldn't find the self-relocation chain head in "
                         f"{a.model} - pass --table explicitly")
    nodes = walk_chain(d, head)
    texs = find_textures(d, nodes=nodes)
    material_tags, _, _, cull_state = compute_material_tags(d, texs)

    fbx_path = Path(a.fbx)
    fbx_text = fbx_path.read_text(encoding="utf-8", errors="surrogateescape")
    fbx_text, bone_renamed = rename_meshes_to_room_by_bone(fbx_text)

    if a.displaylist:
        room_tags, room_mixed = room_light_tags_from_dump(Path(a.displaylist).read_text(
            encoding="utf-8", errors="surrogateescape"))
    else:
        # fallback: use the fbx's own connect lines instead
        room_map = parse_material_room_map(fbx_text)
        room_tags, room_mixed = compute_room_light_tags(d, nodes, texs, room_map)

    texture_setups = compute_texture_setup_correlation(d, nodes, texs)
    new_text, mat_changed = fix_fbx(fbx_text, material_tags, cull_state)

    # room_tags/room_mixed cover the whole character, but this fbx
    # might only have some of those rooms (e.g. a Special Part export)
    present_rooms = set(re.findall(r'Model: "Model::(Room[0-9A-Fa-f]{2})[^"]*", "Mesh"', new_text))
    room_tags = {r: v for r, v in room_tags.items() if r in present_rooms}
    room_mixed = {r: v for r, v in room_mixed.items() if r in present_rooms}

    split_results = {}
    still_mixed = {}
    for room, setups in room_mixed.items():
        split_text, new_names, resolved_setup, reason = split_mixed_room(
            new_text, room, texture_setups, setups)
        if split_text is None:
            if resolved_setup is not None:
                room_tags[room] = resolved_setup
            else:
                still_mixed[room] = reason
            continue
        new_text = split_text
        split_results[room] = new_names
    room_mixed = still_mixed

    new_text, room_changed = fix_rooms(new_text, room_tags)
    new_text, default_mat_removed = remove_unused_default_material(new_text)

    if a.output:
        out_path = Path(a.output)
    elif a.in_place:
        out_path = fbx_path
    else:
        out_path = fbx_path.with_name(f"{fbx_path.stem}_fixed{fbx_path.suffix}")

    out_path.write_text(new_text, encoding="utf-8", errors="surrogateescape")

    if bone_renamed:
        print(f"{len(bone_renamed)} mesh(es) renamed to match their bound bone:")
        for old, new in bone_renamed:
            print(f"  {old} -> {new}")
        print()

    print(f"{len(mat_changed)} material name(s) updated -> {out_path}")
    for addr, old, new in mat_changed:
        old_disp = old if old else "(none)"
        new_disp = new if new else "(none)"
        print(f"  0x{addr}  {old_disp} -> {new_disp}")
    if not mat_changed:
        print("  (no Material:: names needed a tag change)")

    if default_mat_removed:
        print(f"\nDefaultMaterial removed (confirmed unused) from {len(default_mat_removed)} "
              f"mesh(es): {', '.join(default_mat_removed)}")

    print(f"\n{len(room_changed)} room name(s) updated with LightColor/ShadowColor:")
    for room, old, new in room_changed:
        old_disp = old if old else "(none)"
        print(f"  {room}  {old_disp} -> {new}")
    if not room_changed:
        print("  (none)")

    if split_results:
        print(f"\n{len(split_results)} room(s) with mixed light/shadow colors "
              "split into separate objects:")
        for room, names in split_results.items():
            print(f"  {room} -> {', '.join(names)}")

    if room_mixed:
        print(f"\n{len(room_mixed)} room(s) left untouched (couldn't be split "
              "with confidence):")
        for room in sorted(room_mixed):
            print(f"  {room}: {room_mixed[room]}")


if __name__ == "__main__":
    main()
