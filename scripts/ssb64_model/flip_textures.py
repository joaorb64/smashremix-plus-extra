"""Turn an export made before textures were flipped upright (export_model.TEX_FLIP_V) into one
that has them: every PNG in OUT_DIR/textures/ flipped vertically, model.gltf's V flipped
(v -> 1 - v), manifest.json 'tex_flip_v', and (run in Blender) the .blend's UV maps flipped to
match, its images reloaded, its materials' texture nodes redone (n64_panel.update_view).

    ssb64_model.py flip-textures OUT_DIR [--blend FILE]

What the model looks like doesn't change; the images on disk are upright to edit. Other .blend
files in the folder (backups) aren't converted: they still expect the old images.
"""
import json
import os
import struct
import sys

COMP = {5126: ('f', 4), 5121: ('B', 1), 5123: ('H', 2)}
SKIP_UV = ('N64_RG', 'N64_BA')          # vertex colors carried as UVs (blend_to_gltf), not texture coordinates


def flip_pngs(folder):
    from PIL import Image
    n = 0
    tex = os.path.join(folder, 'textures')
    for name in sorted(os.listdir(tex)) if os.path.isdir(tex) else []:
        if not name.lower().endswith('.png'):
            continue
        p = os.path.join(tex, name)
        im = Image.open(p)
        im.load()
        info = {k: v for k, v in im.info.items() if k in ('transparency',)}
        im.transpose(Image.FLIP_TOP_BOTTOM).save(p, **info)
        n += 1
    return n


def flip_gltf(path):
    """V of every TEXCOORD_0 accessor (float) in the glTF's binary buffer, in place."""
    gl = json.load(open(path))
    done = set()
    for m in gl.get('meshes', []):
        for pr in m.get('primitives', []):
            a = pr.get('attributes', {}).get('TEXCOORD_0')
            if a is None or a in done:
                continue
            done.add(a)
            acc = gl['accessors'][a]
            if acc.get('componentType') != 5126 or acc.get('type') != 'VEC2':
                raise SystemExit(f'{path}: TEXCOORD_0 accessor {a} isn\'t float VEC2')
            bv = gl['bufferViews'][acc['bufferView']]
            buf = gl['buffers'][bv['buffer']]
            bpath = os.path.join(os.path.dirname(path), buf['uri'])
            data = bytearray(open(bpath, 'rb').read())
            stride = bv.get('byteStride') or 8
            base = bv.get('byteOffset', 0) + acc.get('byteOffset', 0)
            for i in range(acc['count']):
                o = base + i * stride + 4
                v, = struct.unpack_from('<f', data, o)
                struct.pack_into('<f', data, o, 1.0 - v)
            open(bpath, 'wb').write(data)
            if 'min' in acc and 'max' in acc:
                acc['min'][1], acc['max'][1] = 1.0 - acc['max'][1], 1.0 - acc['min'][1]
    open(path, 'w').write(json.dumps(gl, indent=1))
    return len(done)


def convert_files(folder):
    mp = os.path.join(folder, 'manifest.json')
    man = json.load(open(mp))
    if man.get('tex_flip_v'):
        raise SystemExit(f'{folder}: its textures are already upright (manifest tex_flip_v)')
    n = flip_pngs(folder)
    k = flip_gltf(os.path.join(folder, 'model.gltf'))
    man['tex_flip_v'] = True
    open(mp, 'w').write(json.dumps(man, indent=1))
    print(f'  flipped {n} PNGs, {k} UV sets in model.gltf; manifest tex_flip_v')


# ---------------------------------------------------------------------------- inside Blender

def convert_blend():
    import bpy
    import numpy as np
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import n64_panel as PANEL
    scene = bpy.context.scene
    if scene.get('n64_tex_flip_v'):
        print('  the .blend is already converted')
        return
    tex_dir = os.path.normcase(os.path.abspath(bpy.path.abspath('//textures')))
    for me in bpy.data.meshes:
        for layer in me.uv_layers:
            if layer.name in SKIP_UV or not len(layer.data):
                continue
            a = np.empty(len(layer.data) * 2, np.float32)
            layer.data.foreach_get('uv', a)
            a[1::2] = 1.0 - a[1::2]
            layer.data.foreach_set('uv', a)
    for img in bpy.data.images:
        if img.source != 'FILE' and img.source != 'GENERATED':
            continue
        path = os.path.normcase(os.path.abspath(bpy.path.abspath(img.filepath))) if img.filepath else ''
        on_disk = path.startswith(tex_dir) and os.path.exists(path)
        if on_disk and img.packed_file is None and not img.is_dirty:
            img.reload()                               # the flipped PNG
        elif img.size[0] and img.size[1]:              # packed / painted / generated: its pixels
            w, h = img.size
            px = np.empty(w * h * 4, np.float32)
            img.pixels.foreach_get(px)
            img.pixels.foreach_set(px.reshape(h, w, 4)[::-1].ravel())
            img.update()
            try:                                       # (an image from elsewhere: kept flipped in the .blend)
                img.pack()
            except RuntimeError:
                pass
    scene['n64_tex_flip_v'] = True
    for m in bpy.data.materials:
        if m.node_tree is not None:
            try:
                PANEL.update_view(m)
            except Exception as e:      # noqa: BLE001 (a material the panel can't set up: left as it is)
                print(f'  {m.name}: {e}')
    bpy.ops.wm.save_mainfile()
    print('  .blend: UVs flipped, images reloaded, materials updated')


if __name__ == '__main__' and 'bpy' in sys.modules:
    convert_blend()
