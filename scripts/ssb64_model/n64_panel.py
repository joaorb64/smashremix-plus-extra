"""N64 material settings as a panel (Properties > Material > "N64 Model"), for every
model ssb64_model exports (fighters, items, projectiles, stages), and scene light
colors (Properties > Scene > "N64 Model"), with dropdowns, toggles and color pickers
instead of typed text.

They edit the same custom properties the import reads (n64_format,
n64_wrap_s, ...), so a .blend works the same with or without this panel.

build_blend.py embeds this file in the .blend as a text block that registers
itself when Blender is allowed to run the file's scripts. It also installs as an
add-on: Edit > Preferences > Add-ons > Install from Disk.
"""
import os
import re
import math

import bpy
import mathutils

bl_info = {
    'name': 'N64 Model Materials',
    'author': 'smashremix-extra',
    'version': (1, 0, 0),
    'blender': (3, 6, 0),
    'location': 'Properties > Material / Scene > N64 Model',
    'description': 'Texture format, wrap, culling, lighting and light colors for ssb64_model',
    'category': 'Material',
}

FORMATS = ['CI4', 'CI8', 'IA4', 'IA8', 'IA16', 'I4', 'I8', 'RGBA16']
WRAPS = ['clamp', 'repeat', 'mirror']
CULLS = ['back', 'none', 'front', 'both']


def text_enum(key, options, default, labels=None, descriptions=None, after_set=None, name='', description=''):
    """An enum that reads / writes the string custom property `key`."""
    items = [(o, (labels or {}).get(o, o), (descriptions or {}).get(o, '')) for o in options]

    def get(self):
        v = self.get(key, default() if callable(default) else default)
        return options.index(v) if v in options else 0

    def set(self, i):
        self[key] = options[i]
        if after_set:
            after_set(self, options[i])
        if isinstance(self, bpy.types.Material):
            update_view(self)
    return bpy.props.EnumProperty(items=items, get=get, set=set, name=name, description=description)


def hex_color(key, default, name='', description=''):
    """An sRGB color picker that reads / writes the hex RGB string custom property `key`."""
    def get(self):
        h = str(self.get(key, default)).lstrip('#')
        return [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]

    def set(self, rgb):
        self[key] = ''.join(f'{max(0, min(255, round(c * 255))):02X}' for c in rgb)
        if isinstance(self, bpy.types.Material):
            update_view(self)
        elif isinstance(self, bpy.types.Scene):          # the model's light colors: every material
            for mat in bpy.data.materials:
                update_view(mat)
    return bpy.props.FloatVectorProperty(subtype='COLOR_GAMMA', size=3, min=0.0, max=1.0, get=get, set=set,
                                         name=name, description=description)


def own_flag(key, default, name='', description=''):
    """Toggle: does this material have its own value for `key` (else the model's)?"""
    def get(self):
        return key in self.keys()

    def set(self, on):
        if on and key not in self.keys():
            self[key] = default
        elif not on and key in self.keys():
            del self[key]
        update_view(self)
    return bpy.props.BoolProperty(get=get, set=set, name=name, description=description)


def _sync_culling(mat, cull):
    mat.use_backface_culling = cull in ('back', 'both')        # the viewport shows it too


def _cull_default(mat):
    return 'back' if mat.use_backface_culling else 'none'


def _cull_get(self):
    v = self.get('n64_cull')
    if v not in ('front', 'both'):                            # back / none: the checkbox decides
        v = _cull_default(self)
    return CULLS.index(v)


def _cull_set(self, i):
    self['n64_cull'] = CULLS[i]
    _sync_culling(self, CULLS[i])


def _lit_get(self):
    return self.get('n64_lighting', 'on') != 'off'


def _lit_set(self, on):
    self['n64_lighting'] = 'on' if on else 'off'
    update_view(self)


# ---------------------------------------------------------------- viewport
# The settings also show in Blender: wrap per axis (UV math nodes; Material Preview /
# Rendered view; the Solid view only has one mode per image), culling (Backface
# Culling), unlit = texture x vertex color as emission, own light color as a tint.
WRAP_EXTENSION = {'clamp': 'EXTEND', 'repeat': 'REPEAT', 'mirror': 'MIRROR'}


def _node(nt, name, kind, x, y):
    n = nt.nodes.get(name)
    if n is None:
        n = nt.nodes.new(kind)
        n.name = n.label = name
        n.location = (x, y)
    return n


def _image_node(m):
    if m.node_tree is None:
        return None
    return next((n for n in m.node_tree.nodes if n.type == 'TEX_IMAGE' and n.image
                 and not n.name.startswith('N64 ')), None)


# ---------------------------------------------------------------- material animation
# Stages / items: an MObj's material script animates these (matanim.py: texture flips,
# scrolling, palette / color changes). Each is an animatable custom property on the
# material: keyframe it (hover + I) and the build writes the script; the game plays 60
# frames per second. (property, label, kind, default, description)
ANIM_PROPS = [
    ('n64_tex_index', 'Image', 'index', 0, 'Which image of the material\'s list it draws (the N64 Frame '
     'nodes): animate it to flip through them'),
    ('n64_pal_index', 'Palette', 'index', 0, 'Which palette of the material\'s list it draws with'),
    ('n64_offset_x', 'Offset X', 'float', 0.0, 'Texture position, in texture widths (animate it to scroll)'),
    ('n64_offset_y', 'Offset Y', 'float', 0.0, 'Texture position, in texture heights (animate it to scroll)'),
    ('n64_scale_x', 'Scale X', 'float', 1.0, 'Texture scale X (divides the offset)'),
    ('n64_scale_y', 'Scale Y', 'float', 1.0, 'Texture scale Y (divides the offset)'),
    ('n64_scroll2_x', 'Second Tile X', 'float', 0.0, 'Offset of the second texture tile (two-texture combiners)'),
    ('n64_scroll2_y', 'Second Tile Y', 'float', 0.0, 'Offset of the second texture tile (two-texture combiners)'),
    ('n64_tex_next', 'Second Image', 'index', 0, 'The second image (blended by the LOD fraction)'),
    ('n64_lod_frac', 'LOD Fraction', 'float', 0.0, 'Prim LOD fraction (combiner input PRIM_LOD_FRAC)'),
    ('n64_prim_color', 'Prim Color', 'color', (1.0, 1.0, 1.0, 1.0), 'The MObj\'s prim color (RGBA)'),
    ('n64_env_color', 'Env Color', 'color', (1.0, 1.0, 1.0, 1.0), 'The MObj\'s env color (RGBA)'),
    ('n64_blend_color', 'Blend Color', 'color', (1.0, 1.0, 1.0, 1.0), 'The MObj\'s blend color (RGBA)'),
    ('n64_light1_color', 'Light Color', 'color', (1.0, 1.0, 1.0, 1.0), 'The MObj\'s light color (RGBA)'),
    ('n64_light2_color', 'Shadow Color', 'color', (0.5, 0.5, 0.5, 1.0), 'The MObj\'s shadow (ambient) color'),
]
ANIM_INFO = {p[0]: p for p in ANIM_PROPS}


def anim_fcurves(idb, create=False):
    """The ID's action F-curves (Blender 4.4+: its slot's channelbag), or None."""
    ad = idb.animation_data
    if ad is None:
        if not create:
            return None
        ad = idb.animation_data_create()
    act = ad.action
    if act is None:
        if not create:
            return None
        act = ad.action = bpy.data.actions.new(f'{idb.name} N64')
    if hasattr(act, 'slots') and hasattr(ad, 'action_slot'):
        slot = ad.action_slot
        if slot is None:
            if not create:
                return None
            slot = act.slots.new(id_type=type(idb).__name__.upper(), name=idb.name)
            ad.action_slot = slot
        from bpy_extras import anim_utils
        if create:
            return anim_utils.action_ensure_channelbag_for_slot(act, slot).fcurves
        cb = anim_utils.action_get_channelbag_for_slot(act, slot)
        return cb.fcurves if cb is not None else None
    return act.fcurves


def anim_prop_create(m, prop, value=None):
    """Material m's animatable property `prop` (with its starting value)."""
    _, label, kind, default, desc = ANIM_INFO[prop]
    if prop in m.keys():
        return
    if value is None:
        value = default
    if kind == 'color':
        if isinstance(value, int):                       # an RGBA word
            value = [((value >> sh) & 255) / 255 for sh in (24, 16, 8, 0)]
        m[prop] = [float(x) for x in value]
    elif kind == 'index':
        m[prop] = int(value)
    else:
        m[prop] = float(value)
    try:
        ui = m.id_properties_ui(prop)
        if kind == 'color':
            ui.update(subtype='COLOR_GAMMA', min=0.0, max=1.0, description=desc)
        elif kind == 'index':
            ui.update(min=0, max=255, description=desc)
        else:
            ui.update(description=desc, step=1, precision=4)
    except (TypeError, AttributeError):
        pass


def anim_set_keys(m, prop, keys, color=False, cycle=False):
    """Keyframes [[frame, value, 'CONSTANT' | 'LINEAR'], ...] on m's property (colors: RGBA
    words, one curve per channel); cycle: the curve repeats (the script loops)."""
    fcs = anim_fcurves(m, create=True)
    for i in range(4 if color else 1):
        path = f'["{prop}"]'
        old = fcs.find(path, index=i)
        if old is not None:
            fcs.remove(old)
        fc = fcs.new(path, index=i, group_name='N64 Material')
        fc.keyframe_points.add(len(keys))
        for kp, (t, v, ip) in zip(fc.keyframe_points, keys):
            if color:
                v = ((int(v) >> (24 - 8 * i)) & 255) / 255
            kp.co = (float(t), float(v))
            kp.interpolation = ip
        if cycle:
            fc.modifiers.new('CYCLES')
        fc.update()


def anim_props(m):
    """m's material-animation properties present, in panel order."""
    return [p[0] for p in ANIM_PROPS if p[0] in m.keys()]


def anim_animated(m):
    """{property: [F-curves]} of m's keyframed material-animation properties."""
    fcs = anim_fcurves(m)
    out = {}
    for fc in (fcs or []):
        name = fc.data_path[2:-2] if fc.data_path.startswith('["') else None
        if name in ANIM_INFO and len(fc.keyframe_points):
            out.setdefault(name, []).append(fc)
    return out


def frame_nodes(m, kind='Frame'):
    """The material's 'N64 Frame <i>' (image list) / 'N64 Palette <i>' texture nodes, by index."""
    if m.node_tree is None:
        return []
    out = {}
    for n in m.node_tree.nodes:
        if n.type == 'TEX_IMAGE' and n.name.startswith(f'N64 {kind} '):
            try:
                out[int(n.name.split()[-1])] = n
            except ValueError:
                pass
    return [out[i] for i in sorted(out)]


def frame_node(m, kind, i):
    nt = m.node_tree
    name = f'N64 {kind} {i}'
    n = nt.nodes.get(name)
    if n is None:
        n = nt.nodes.new('ShaderNodeTexImage')
        n.name = n.label = name
        tex = _tex_node(m)
        x, y = tex.location if tex is not None else (-600, 0)
        n.location = (x - 300 * (1 + (kind == 'Palette')), y - 280 * (i + 1))
        n.hide = True
    return n


def _drive(owner, path, m, expr, variables, index=-1, id_type='MATERIAL'):
    """A driver on owner.path (a node socket value) computing expr from m's properties
    (id_type 'SCENE': m is the scene)."""
    fc = owner.driver_add(path, index) if index >= 0 else owner.driver_add(path)
    drv = fc.driver
    drv.type = 'SCRIPTED'
    for v in list(drv.variables):
        drv.variables.remove(v)
    for name, data_path in variables.items():
        var = drv.variables.new()
        var.name, var.type = name, 'SINGLE_PROP'
        var.targets[0].id_type = id_type
        var.targets[0].id = m
        var.targets[0].data_path = data_path
    drv.expression = expr
    return fc


def _clear_drivers(nt, prefix):
    ad = nt.animation_data
    if ad is None:
        return
    for fc in list(ad.drivers):
        if f'nodes["{prefix}' in fc.data_path:
            ad.drivers.remove(fc)


def _offset_exprs(m):
    """Driver expressions for the tile offset the MObj draws with (objdisplay.c: tile 0's
    upper-left from trau / trav / scau / scav), in Blender UV units, or None."""
    info = m.get('n64_mobj')
    if not info or not (int(info.get('flags', 0)) & 0x20):
        return None
    if not all(p in m.keys() for p in ('n64_offset_x', 'n64_offset_y', 'n64_scale_x', 'n64_scale_y')):
        return None
    w, h = (int(x) or 1 for x in info.get('tex_size', (32, 32)))
    cs, ct = (int(x) for x in info.get('size0', (w, h)))
    a = int(info.get('unk0a', 0))
    # the RDP subtracts the tile's upper-left from s, t: u - uls / w; Blender's v is 1 - t / h,
    # so v + ult / h
    if int(info.get('mode', 0)) == 2:
        ex = f'-(max(0.0, {cs}*tu/su) if abs(su) > 1.5e-5 else 0.0)/{w}'
        ey = f'(max(0.0, {ct}*tv/sv) if abs(sv) > 1.5e-5 else 0.0)/{h}'
    else:
        ex = f'-(({cs}*tu+{a})/su if abs(su) > 1.5e-5 else 0.0)/{w}'
        ey = f'(((1.0-sv-tv)*{ct}+{a})/sv if abs(sv) > 1.5e-5 else 0.0)/{h}'
    v = {'tu': '["n64_offset_x"]', 'tv': '["n64_offset_y"]', 'su': '["n64_scale_x"]', 'sv': '["n64_scale_y"]'}
    return ex, ey, v


def update_view(m, context=None):
    """Make the material's look in Blender follow its n64_* settings."""
    if not isinstance(m, bpy.types.Material) or m.node_tree is None:
        return
    nt = m.node_tree
    tex = _image_node(m)
    out = next((n for n in nt.nodes if n.type == 'OUTPUT_MATERIAL'), None)
    bsdf = next((n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if tex is not None:
        ws, wt = m.get('n64_wrap_s', 'clamp'), m.get('n64_wrap_t', 'clamp')
        try:                                   # Solid view: one mode for both axes
            tex.extension = WRAP_EXTENSION['mirror' if 'mirror' in (ws, wt) else
                                           'repeat' if 'repeat' in (ws, wt) else 'clamp']
        except TypeError:
            tex.extension = 'REPEAT'
        tex.interpolation = 'Closest' if m.get('n64_filter') == 'point' else 'Linear'   # the game filters
        x, y = tex.location
        uv = _node(nt, 'N64 UV', 'ShaderNodeUVMap', x - 900, y)
        sep = _node(nt, 'N64 Separate UV', 'ShaderNodeSeparateXYZ', x - 700, y)
        comb = _node(nt, 'N64 Combine UV', 'ShaderNodeCombineXYZ', x - 200, y)
        nt.links.new(uv.outputs['UV'], sep.inputs[0])
        offs = _offset_exprs(m)
        _clear_drivers(nt, 'N64 Offset')
        for i, (axis, mode) in enumerate((('X', ws), ('Y', wt))):
            mth = _node(nt, f'N64 Wrap {axis}', 'ShaderNodeMath', x - 450, y + 120 - 240 * i)
            src = sep.outputs[axis]
            if offs:                           # the MObj's tile offset (animated: scrolling)
                off = _node(nt, f'N64 Offset {axis}', 'ShaderNodeMath', x - 900, y + 180 - 240 * i)
                off.operation = 'ADD'
                nt.links.new(src, off.inputs[0])
                _drive(off.inputs[1], 'default_value', m, offs[i], offs[2])
                src = off.outputs[0]
            elif nt.nodes.get(f'N64 Offset {axis}') is not None:
                nt.nodes.remove(nt.nodes[f'N64 Offset {axis}'])
            if axis == 'Y':
                # glTF import flips V (v = 1 - t): wrap / clamp in the N64's t, flip back after.
                # (Clamping at a tile extent isn't symmetric, so this matters: DK's fur.)
                fin = _node(nt, 'N64 Flip In Y', 'ShaderNodeMath', x - 820, y - 120)
                fin.operation, fin.inputs[0].default_value = 'SUBTRACT', 1.0
                nt.links.new(src, fin.inputs[1])
                src = fin.outputs[0]
            clamp = m.get('n64_clamp_' + 'st'[i])
            # clamps stop at the edge texel's center (the N64's clamped coordinate): exactly at
            # the edge, the image node's repeat / mirror extension would read the other side
            size = tex.image.size[i] if tex.image is not None and tex.image.size[i] else 32
            half = 0.5 / size
            if clamp and mode != 'clamp':      # the tile clamps first, past the texture's size
                cl = _node(nt, f'N64 Clamp {axis}', 'ShaderNodeClamp', x - 580, y + 120 - 240 * i)
                cl.inputs['Min'].default_value, cl.inputs['Max'].default_value = 0.0, float(clamp) - half
                nt.links.new(src, cl.inputs['Value'])
                src = cl.outputs[0]
            else:
                cl = nt.nodes.get(f'N64 Clamp {axis}')
                if cl is not None:
                    nt.nodes.remove(cl)
            mth.use_clamp = False
            edge = None
            if mode == 'repeat':
                mth.operation = 'WRAP'
                mth.inputs[1].default_value, mth.inputs[2].default_value = 1.0, 0.0
            elif mode == 'mirror':
                mth.operation = 'PINGPONG'
                mth.inputs[1].default_value = 1.0
            else:                              # clamp (at the tile's extent when inside the texture)
                mth.operation = 'MAXIMUM'
                mth.inputs[1].default_value = half
                edge = _node(nt, f'N64 Edge {axis}', 'ShaderNodeMath', x - 380, y + 160 - 240 * i)
                edge.operation = 'MINIMUM'
                edge.inputs[1].default_value = (float(clamp) if clamp and clamp < 1 else 1.0) - half
            if edge is None and nt.nodes.get(f'N64 Edge {axis}') is not None:
                nt.nodes.remove(nt.nodes[f'N64 Edge {axis}'])
            nt.links.new(src, mth.inputs[0])
            res = mth.outputs[0]
            if edge is not None:
                nt.links.new(res, edge.inputs[0])
                res = edge.outputs[0]
            if axis == 'Y':
                fout = _node(nt, 'N64 Flip Out Y', 'ShaderNodeMath', x - 320, y - 120)
                fout.operation, fout.inputs[0].default_value = 'SUBTRACT', 1.0
                nt.links.new(res, fout.inputs[1])
                res = fout.outputs[0]
            nt.links.new(res, comb.inputs[axis])
        nt.links.new(comb.outputs[0], tex.inputs['Vector'])
        for kind in ('Frame', 'Palette'):      # the image / palette list entries: same sampling
            for n in frame_nodes(m, kind):
                _use_file(n.image)
                nt.links.new(comb.outputs[0], n.inputs['Vector'])
                n.interpolation, n.extension = tex.interpolation, tex.extension
    if out is None or bsdf is None:
        return
    build_combiner(m, nt, tex, out, bsdf)


def _srgb_lin(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _recolored(src, pal0, palp, name):
    """Image src (drawn in palette 0's colors) in palette p's: each color of palette 0's
    image becomes the color at the same pixels of palette p's (a preview: indices that
    share a color in palette 0 take one of their palette-p colors). In memory only."""
    import numpy as np

    def px(img):
        n = len(img.pixels)                    # (loads it)
        a = np.empty(n, np.float32)
        if n:
            img.pixels.foreach_get(a)
        return a.reshape(-1, 4)
    a, b, s_ = px(pal0), px(palp), px(src)
    if not len(a) or not len(s_) or len(a) != len(b):
        return src
    key = lambda c: (np.round(c * 255).astype(np.int64) * [1 << 24, 1 << 16, 1 << 8, 1]).sum(-1)  # noqa: E731
    lut = dict(zip(key(a).tolist(), b.tolist()))
    out = np.array([lut.get(k, c) for k, c in zip(key(s_).tolist(), s_.tolist())], np.float32)
    img = bpy.data.images.get(name)
    if img is None or tuple(img.size) != tuple(src.size):
        if img is not None:
            bpy.data.images.remove(img)
        img = bpy.data.images.new(name, src.size[0], src.size[1], alpha=True)
    img.pixels.foreach_set(out.ravel())
    img.update()
    try:
        img.pack()                       # (a generated image's pixels aren't saved otherwise)
    except RuntimeError:
        pass
    return img


def _anim_texture(m, nt, tex, node, tex_c, tex_a):
    """The texture's color / alpha as the animated Image (N64 Frame nodes) and Palette
    (N64 Palette nodes) properties pick it; both: image i recolored to palette p."""
    if tex is None:
        return tex_c, tex_a
    frames = frame_nodes(m) if 'n64_tex_index' in m.keys() else []
    pals = frame_nodes(m, 'Palette') if 'n64_pal_index' in m.keys() else []
    frames = frames if len(frames) > 1 else []
    pals = pals if len(pals) > 1 and all(n.image is not None for n in pals) else []
    if not frames and not pals:
        return tex_c, tex_a
    picks = {}

    def pick(prop, i):
        if prop not in picks:
            idx = node('ShaderNodeValue')
            _drive(idx.outputs[0], 'default_value', m, 'floor(i)', {'i': f'["{prop}"]'})
            picks[prop] = idx.outputs[0]
        c = node('ShaderNodeMath', operation='COMPARE')
        nt.links.new(picks[prop], c.inputs[0])
        c.inputs[1].default_value, c.inputs[2].default_value = float(i), 0.25
        return c.outputs[0]
    uv = nt.nodes.get('N64 Combine UV')
    entries = []                         # (texture node, [pick sockets])
    if frames and pals:                  # every image in every palette
        for i, fr in enumerate(frames):
            for p, pl in enumerate(pals):
                if fr.image is None:
                    continue
                img = fr.image if p == 0 else _recolored(fr.image, pals[0].image, pl.image,
                                                         f'{m.name} N64 image{i} palette{p}')
                t = node('ShaderNodeTexImage')
                t.image, t.interpolation, t.extension = img, tex.interpolation, tex.extension
                if uv is not None:
                    nt.links.new(uv.outputs[0], t.inputs['Vector'])
                entries.append((t, [pick('n64_tex_index', i), pick('n64_pal_index', p)]))
    else:
        prop = 'n64_tex_index' if frames else 'n64_pal_index'
        entries = [(n, [pick(prop, i)]) for i, n in enumerate(frames or pals)]
    tex_c, tex_a = entries[0][0].outputs['Color'], entries[0][0].outputs['Alpha']
    for t, sel in entries[1:]:
        f = sel[0]
        if len(sel) > 1:
            both = node('ShaderNodeMath', operation='MULTIPLY')
            nt.links.new(sel[0], both.inputs[0])
            nt.links.new(sel[1], both.inputs[1])
            f = both.outputs[0]
        mc = node('ShaderNodeMix', data_type='RGBA', blend_type='MIX')
        nt.links.new(f, mc.inputs['Factor'])
        nt.links.new(tex_c, mc.inputs['A'])
        nt.links.new(t.outputs['Color'], mc.inputs['B'])
        ma = node('ShaderNodeMix', data_type='FLOAT')
        nt.links.new(f, ma.inputs['Factor'])
        nt.links.new(tex_a, ma.inputs['A'])
        nt.links.new(t.outputs['Alpha'], ma.inputs['B'])
        tex_c, tex_a = mc.outputs['Result'], ma.outputs['Result']
    return tex_c, tex_a


# the game's light (ftdisplaylights.c): from the stage's light_angle (x, y degrees; fighters,
# items: the usual 20, 45). Shade = ambient (shadow color) + light color x max(0, N . L), in
# the game's (sRGB) values, then to Blender's linear colors
DEFAULT_LIGHT_ANGLE = (20.0, 45.0)


def _hex_rgb(h, default):
    try:
        v = int(str(h or default).lstrip('#'), 16)
    except ValueError:
        v = int(default, 16)
    return [((v >> sh) & 255) / 255 for sh in (16, 8, 0)]


def _game_shade(m, node, nt):
    scn = bpy.context.scene
    light = _hex_rgb(m.get('n64_light_color') or m.get('n64_costume_light') or
                     (scn.get('n64_light_color') if scn else None), 'FFFFFF')
    shadow = _hex_rgb(m.get('n64_shadow_color') or m.get('n64_costume_shadow') or
                      (scn.get('n64_shadow_color') if scn else None), '808080')
    geo = node('ShaderNodeNewGeometry')
    ldir = node('ShaderNodeCombineXYZ')
    if scn is not None and 'n64_light_angle' not in scn.keys():
        scn['n64_light_angle'] = list(DEFAULT_LIGHT_ANGLE)
    # game direction (x, y, z) = (sin ax cos ay, sin ay, cos ax cos ay); Blender (x, -z, y)
    r = 0.017453292519943295
    exprs = (f'sin(ax*{r})*cos(ay*{r})', f'-cos(ax*{r})*cos(ay*{r})', f'sin(ay*{r})')
    for i, e in enumerate(exprs):
        if scn is not None:
            _drive(ldir.inputs[i], 'default_value', scn, e,
                   {'ax': '["n64_light_angle"][0]', 'ay': '["n64_light_angle"][1]'}, id_type='SCENE')
    dot = node('ShaderNodeVectorMath', operation='DOT_PRODUCT')
    nt.links.new(geo.outputs['Normal'], dot.inputs[0])
    nt.links.new(ldir.outputs[0], dot.inputs[1])
    ndl = node('ShaderNodeMath', operation='MAXIMUM')
    nt.links.new(dot.outputs['Value'], ndl.inputs[0])
    ndl.inputs[1].default_value = 0.0
    lc = node('ShaderNodeRGB')
    lc.outputs[0].default_value = (*light, 1.0)
    sc = node('ShaderNodeRGB')
    sc.outputs[0].default_value = (*shadow, 1.0)
    lit = node('ShaderNodeVectorMath', operation='SCALE')
    nt.links.new(lc.outputs[0], lit.inputs[0])
    nt.links.new(ndl.outputs[0], lit.inputs['Scale'])
    add = node('ShaderNodeVectorMath', operation='ADD')
    nt.links.new(lit.outputs[0], add.inputs[0])
    nt.links.new(sc.outputs[0], add.inputs[1])
    cl = node('ShaderNodeMix', data_type='RGBA', blend_type='MIX', clamp_result=True)
    cl.inputs['Factor'].default_value = 1.0
    nt.links.new(add.outputs[0], cl.inputs['B'])
    gam = node('ShaderNodeGamma')                 # the game's values -> linear
    nt.links.new(cl.outputs['Result'], gam.inputs['Color'])
    gam.inputs['Gamma'].default_value = 2.2
    return gam.outputs[0]


def _format_view(m, node, nt, color, alpha):
    """The texture as its n64_format stores it: intensity formats are gray (I: alpha = the
    intensity too), each channel cut to its bits (truncated, like the build), 1-bit alpha
    (IA4, RGBA16, CI palettes). CI formats keep their colors (the build reduces them to
    16 / 256)."""
    fmt = str(m.get('n64_format', 'CI4'))
    if fmt == 'RGBA32':
        return color, alpha

    def math(op, a, b=None):
        n = node('ShaderNodeMath', operation=op)
        if isinstance(a, float):
            n.inputs[0].default_value = a
        else:
            nt.links.new(a, n.inputs[0])
        if b is not None:
            if isinstance(b, float):
                n.inputs[1].default_value = b
            else:
                nt.links.new(b, n.inputs[1])
        return n.outputs[0]

    def bits(x, b):                      # 8-bit value cut to b bits, read back (n * 255 / (2^b - 1))
        if b >= 8:
            return x
        return math('DIVIDE', math('FLOOR', math('MULTIPLY', x, 255.0 / (1 << (8 - b)))), float((1 << b) - 1))

    def one_bit(a):
        return math('GREATER_THAN', a, 0.4999)
    if fmt.startswith('CI'):             # palette colors are RGBA5551: alpha on / off
        return color, one_bit(alpha)
    g = node('ShaderNodeGamma')          # linear -> the game's values
    nt.links.new(color, g.inputs['Color'])
    g.inputs['Gamma'].default_value = 1 / 2.2
    srgb = g.outputs[0]
    if fmt == 'RGBA16':
        sep = node('ShaderNodeSeparateXYZ')
        nt.links.new(srgb, sep.inputs[0])
        comb = node('ShaderNodeCombineXYZ')
        for k in range(3):
            nt.links.new(bits(sep.outputs[k], 5), comb.inputs[k])
        out_c, out_a = comb.outputs[0], one_bit(alpha)
    else:
        lum = node('ShaderNodeVectorMath', operation='DOT_PRODUCT')
        nt.links.new(srgb, lum.inputs[0])
        lum.inputs[1].default_value = (0.299, 0.587, 0.114)
        ib, ab = {'I4': (4, None), 'I8': (8, None), 'IA4': (3, 1), 'IA8': (4, 4), 'IA16': (8, 8)}.get(fmt, (8, 8))
        i = bits(lum.outputs['Value'], ib)
        gray = node('ShaderNodeCombineXYZ')
        for k in range(3):
            nt.links.new(i, gray.inputs[k])
        out_c = gray.outputs[0]
        out_a = i if ab is None else (one_bit(alpha) if ab == 1 else bits(alpha, ab))
    back = node('ShaderNodeGamma')
    nt.links.new(out_c, back.inputs['Color'])
    back.inputs['Gamma'].default_value = 2.2
    return back.outputs[0], out_a


def _edge_alpha(m, cmds, node, nt, alpha):
    """How transparent texels show, by the render mode (E200001C) and alpha compare
    (E2001E01): -> (blended?, alpha socket).
      translucent modes: alpha blended
      alpha compare: drawn only where alpha > the blend color's alpha (hard edge)
      anti-aliased texture edge (CVG_X_ALPHA + ALPHA_CVG_SEL + AA_EN): coverage = alpha in
        eighths, blended at the edges: the soft border of the game's sprites (depth-writing
        modes are dithered in Blender, which can't sort blended surfaces by depth)"""
    rm = next((b for a, b in cmds if a == 0xE200001C), None)
    if rm is not None and (rm >> 16) & 3 == 0:
        return True, alpha
    compare = next((b & 3 for a, b in cmds if a == 0xE2001E01), 0)
    thr = next(((b & 255) / 255 for a, b in cmds if a >> 24 == 0xF9), 0.0) if compare == 1 else None
    cvg_x_alpha = (rm is not None and rm & 0x1000) or any(a == 0xE2001300 and b & 0x1000 for a, b in cmds)
    soft = rm is not None and rm & 0x3008 == 0x3008
    if thr is not None:                              # alpha compare: nothing at or under the threshold
        keep = node('ShaderNodeMath', operation='GREATER_THAN')
        nt.links.new(alpha, keep.inputs[0])
        keep.inputs[1].default_value = thr
        if not soft:
            return False, keep.outputs[0]
        cut = node('ShaderNodeMath', operation='MULTIPLY')
        nt.links.new(alpha, cut.inputs[0])
        nt.links.new(keep.outputs[0], cut.inputs[1])
        alpha = cut.outputs[0]
    if soft:                                         # coverage = alpha in eighths, AA-blended
        q = node('ShaderNodeMath', operation='MULTIPLY')
        nt.links.new(alpha, q.inputs[0])
        q.inputs[1].default_value = 8.0
        fl = node('ShaderNodeMath', operation='FLOOR')
        nt.links.new(q.outputs[0], fl.inputs[0])
        d = node('ShaderNodeMath', operation='DIVIDE')
        nt.links.new(fl.outputs[0], d.inputs[0])
        d.inputs[1].default_value = 8.0
        return not rm & 0x20, d.outputs[0]          # (Z_UPD: written to the depth buffer: dithered)
    if cvg_x_alpha:                                  # coverage x alpha without AA: on / off
        clip = node('ShaderNodeMath', operation='GREATER_THAN')
        nt.links.new(alpha, clip.inputs[0])
        clip.inputs[1].default_value = 0.124
        return False, clip.outputs[0]
    if rm is not None:      # a mode that neither blends nor takes coverage from the texture alpha
        return False, 1.0   # (e.g. 00552078, AA opaque surface): opaque, whatever the alpha
    return False, alpha


def build_combiner(m, nt, tex, out, bsdf):
    """The material's N64 color combiner as shader nodes: two cycles of (a - b) * c + d,
    color and alpha, with its prim / env colors and the texture. SHADE is Blender's own
    lighting when lit (multiplied by the BSDF), the vertex color when unlit."""
    _clear_drivers(nt, 'N64 CC')
    for n in [n for n in nt.nodes if n.name.startswith('N64 CC') or n.name in (
            'N64 Light Tint', 'N64 Unlit', 'N64 Unlit Color', 'N64 Vertex Color')]:
        nt.nodes.remove(n)
    cmds = parse_state(m)
    w0, w1 = next(((a, b) for a, b in cmds if a >> 24 == 0xFC), (0xFC127E05, 0xFF17F3FF))
    lit = m.get('n64_lighting', 'on') != 'off'
    X, Y = bsdf.location.x - 1500, bsdf.location.y - 700
    k = [0]

    def node(kind, **kw):
        k[0] += 1
        n = nt.nodes.new(kind)
        n.name = n.label = f'N64 CC {k[0]}'
        n.location = (X + 170 * (k[0] % 9), Y - 160 * (k[0] // 9))
        for a, v in kw.items():
            setattr(n, a, v)
        return n

    def rgb(c):
        n = node('ShaderNodeRGB')
        n.outputs[0].default_value = (*c[:3], 1.0)
        return n.outputs[0]

    def val(v):
        n = node('ShaderNodeValue')
        n.outputs[0].default_value = v
        return n.outputs[0]

    def hex_color(op, default):
        v = next((b for a, b in cmds if a >> 24 == op), default)
        return [_srgb_lin(((v >> sh) & 255) / 255) for sh in (24, 16, 8)], (v & 255) / 255
    if tex is None:          # untextured: its color is the Base Color (per costume)
        bc = bsdf.inputs['Base Color'].default_value
        prim_c, prim_a = list(bc[:3]), hex_color(0xFA, 0xFFFFFFFF)[1]
    else:
        prim_c, prim_a = hex_color(0xFA, 0xFFFFFFFF)
    # env default: white for fighters; items / projectiles (scene n64_default_env) fade
    # toward env by its alpha, which the game keeps at 0 unless the item flashes
    scn = bpy.context.scene
    env_c, env_a = hex_color(0xFB, int(str(scn.get('n64_default_env', 'FFFFFFFF')), 16) if scn else 0xFFFFFFFF)
    one_c, zero_c = rgb((1, 1, 1)), rgb((0, 0, 0))
    one, zero = val(1.0), val(0.0)
    tex_c = tex.outputs['Color'] if tex is not None else one_c
    tex_a = tex.outputs['Alpha'] if tex is not None else one
    # material animation: the image list entry / palette its index properties pick
    tex_c, tex_a = _anim_texture(m, nt, tex, node, tex_c, tex_a)
    if tex is not None:                  # how the texture's format stores it (materials.encode_direct)
        tex_c, tex_a = _format_view(m, node, nt, tex_c, tex_a)
    if lit:
        shade_c = _game_shade(m, node, nt)
        shade_a = one
    else:
        vc = node('ShaderNodeVertexColor')
        shade_c, shade_a = vc.outputs['Color'], vc.outputs['Alpha']
    P, Pa, E, Ea = rgb(prim_c), val(prim_a), rgb(env_c), val(env_a)
    # the MObj's colors (animated: material animation) instead of the display list's
    lin = 'c/12.92 if c <= 0.04045 else pow((c+0.055)/1.055, 2.4)'
    for prop, (sock, sock_a) in (('n64_prim_color', (P, Pa)), ('n64_env_color', (E, Ea))):
        if prop in m.keys():
            for ch in range(3):
                _drive(sock, 'default_value', m, lin, {'c': f'["{prop}"][{ch}]'}, index=ch)
            _drive(sock_a, 'default_value', m, 'c', {'c': f'["{prop}"][3]'})
    lod = zero
    if 'n64_lod_frac' in m.keys():
        lod = val(0.0)
        _drive(lod, 'default_value', m, 'c', {'c': '["n64_lod_frac"]'})
    half = rgb((0.5, 0.5, 0.5))
    inv = lambda d: {v: key for key, v in d.items()}  # noqa: E731
    A, B, C, D = inv(CC_A), inv(CC_B), inv(CC_C), inv(CC_D)
    AA, ACc = inv(AC_ABD), inv(AC_C)
    comb_c, comb_a = zero_c, zero
    for cyc in range(2):
        if cyc == 0:
            ca, cb, cc, cd = A.get((w0 >> 20) & 15), B.get((w1 >> 28) & 15), C.get((w0 >> 15) & 31), D.get((w1 >> 15) & 7)
            aa, ab, ac, ad = AA.get((w0 >> 12) & 7), AA.get((w1 >> 12) & 7), ACc.get((w0 >> 9) & 7), AA.get((w1 >> 9) & 7)
        else:
            ca, cb, cc, cd = A.get((w0 >> 5) & 15), B.get((w1 >> 24) & 15), C.get(w0 & 31), D.get((w1 >> 6) & 7)
            aa, ab, ac, ad = AA.get((w1 >> 21) & 7), AA.get((w1 >> 3) & 7), ACc.get((w1 >> 18) & 7), AA.get(w1 & 7)
        csrc = {'COMBINED': comb_c, 'TEXEL0': tex_c, 'TEXEL1': tex_c, 'PRIM': P, 'SHADE': shade_c, 'ENV': E,
                '1': one_c, 'NOISE': half, 'COMBINED_ALPHA': comb_a, 'TEXEL0_ALPHA': tex_a,
                'TEXEL1_ALPHA': tex_a, 'PRIM_ALPHA': Pa, 'SHADE_ALPHA': shade_a, 'ENV_ALPHA': Ea,
                'LOD_FRACTION': one, 'PRIM_LOD_FRAC': lod}
        asrc = {'COMBINED': comb_a, 'TEXEL0': tex_a, 'TEXEL1': tex_a, 'PRIM': Pa, 'SHADE': shade_a, 'ENV': Ea,
                '1': one, 'LOD_FRACTION': one, 'PRIM_LOD_FRAC': lod}
        sub = node('ShaderNodeVectorMath', operation='SUBTRACT')
        nt.links.new(csrc.get(ca, zero_c), sub.inputs[0])
        nt.links.new(csrc.get(cb, zero_c), sub.inputs[1])
        mul = node('ShaderNodeVectorMath', operation='MULTIPLY')
        nt.links.new(sub.outputs[0], mul.inputs[0])
        nt.links.new(csrc.get(cc, zero_c), mul.inputs[1])
        add = node('ShaderNodeVectorMath', operation='ADD')
        nt.links.new(mul.outputs[0], add.inputs[0])
        nt.links.new(csrc.get(cd, zero_c), add.inputs[1])
        clampc = node('ShaderNodeMix', data_type='RGBA', blend_type='MIX', clamp_result=True)
        clampc.inputs['Factor'].default_value = 1.0
        nt.links.new(add.outputs[0], clampc.inputs['B'])
        asub = node('ShaderNodeMath', operation='SUBTRACT')
        nt.links.new(asrc.get(aa, zero), asub.inputs[0])
        nt.links.new(asrc.get(ab, zero), asub.inputs[1])
        amul = node('ShaderNodeMath', operation='MULTIPLY')
        nt.links.new(asub.outputs[0], amul.inputs[0])
        nt.links.new(asrc.get(ac, zero), amul.inputs[1])
        aadd = node('ShaderNodeMath', operation='ADD', use_clamp=True)
        nt.links.new(amul.outputs[0], aadd.inputs[0])
        nt.links.new(asrc.get(ad, zero), aadd.inputs[1])
        comb_c, comb_a = clampc.outputs['Result'], aadd.outputs[0]
    # translucent: the render mode's (cycle 2) blender weighs memory by 1 - alpha (XLU modes:
    # 0x0050...., 0x4410....); opaque / cut-out modes use memory coverage (0x0055...., 0xC411....)
    blending, comb_a = _edge_alpha(m, cmds, node, nt, comb_a)
    if isinstance(comb_a, float):
        comb_a = val(comb_a)
    for attr, value in (('surface_render_method', 'BLENDED' if blending else 'DITHERED'),
                        ('blend_method', 'BLEND' if blending else 'HASHED')):
        try:
            setattr(m, attr, value)
        except (AttributeError, TypeError):
            pass
    if True:                 # the combined color as is (lit: SHADE is the game's lighting)
        em = node('ShaderNodeEmission')
        tr = node('ShaderNodeBsdfTransparent')
        mix = node('ShaderNodeMixShader')
        nt.links.new(comb_c, em.inputs['Color'])
        nt.links.new(comb_a, mix.inputs[0])
        nt.links.new(tr.outputs[0], mix.inputs[1])
        nt.links.new(em.outputs[0], mix.inputs[2])
        nt.links.new(mix.outputs[0], out.inputs['Surface'])


# ---------------------------------------------------------------- display list commands
# A material's extra commands (n64_state: "w0 w1; w0 w1; ..."), drawn before its
# texture setup. Shown decoded, with presets to add.
CC_A = {'COMBINED': 0, 'TEXEL0': 1, 'TEXEL1': 2, 'PRIM': 3, 'SHADE': 4, 'ENV': 5, '1': 6, 'NOISE': 7, '0': 15}
CC_B = {'COMBINED': 0, 'TEXEL0': 1, 'TEXEL1': 2, 'PRIM': 3, 'SHADE': 4, 'ENV': 5, 'CENTER': 6, 'K4': 7, '0': 15}
CC_C = {'COMBINED': 0, 'TEXEL0': 1, 'TEXEL1': 2, 'PRIM': 3, 'SHADE': 4, 'ENV': 5, 'SCALE': 6,
        'COMBINED_ALPHA': 7, 'TEXEL0_ALPHA': 8, 'TEXEL1_ALPHA': 9, 'PRIM_ALPHA': 10, 'SHADE_ALPHA': 11,
        'ENV_ALPHA': 12, 'LOD_FRACTION': 13, 'PRIM_LOD_FRAC': 14, 'K5': 15, '0': 31}
CC_D = {'COMBINED': 0, 'TEXEL0': 1, 'TEXEL1': 2, 'PRIM': 3, 'SHADE': 4, 'ENV': 5, '1': 6, '0': 7}
AC_ABD = {'COMBINED': 0, 'TEXEL0': 1, 'TEXEL1': 2, 'PRIM': 3, 'SHADE': 4, 'ENV': 5, '1': 6, '0': 7}
AC_C = {'LOD_FRACTION': 0, 'TEXEL0': 1, 'TEXEL1': 2, 'PRIM': 3, 'SHADE': 4, 'ENV': 5, 'PRIM_LOD_FRAC': 6, '0': 7}


def encode_cc(c1, a1, c2, a2):
    """Color combiner (two cycles), each (a, b, c, d) = (a - b) * c + d -> (w0, w1)."""
    w0 = (0xFC << 24 | CC_A[c1[0]] << 20 | CC_C[c1[2]] << 15 | AC_ABD[a1[0]] << 12 | AC_C[a1[2]] << 9 |
          CC_A[c2[0]] << 5 | CC_C[c2[2]])
    w1 = (CC_B[c1[1]] << 28 | CC_B[c2[1]] << 24 | AC_ABD[a2[0]] << 21 | AC_C[a2[2]] << 18 | CC_D[c1[3]] << 15 |
          AC_ABD[a1[1]] << 12 | AC_ABD[a1[3]] << 9 | CC_D[c2[3]] << 6 | AC_ABD[a2[1]] << 3 | AC_ABD[a2[3]])
    return w0, w1


def decode_cc(w0, w1):
    inv = lambda d: {v: k for k, v in d.items()}  # noqa: E731
    a, b, c, d, aa, ac = inv(CC_A), inv(CC_B), inv(CC_C), inv(CC_D), inv(AC_ABD), inv(AC_C)

    def term(x, y, z, t):
        x, y, z, t = (str(v).replace('COMBINED', 'cycle1').replace('_ALPHA', ' alpha') for v in (x, y, z, t))
        s = x if y == '0' else f'({x} - {y})'
        s = '0' if z == '0' or s == '0' else (s if z == '1' else f'{s} x {z}')
        return t if s == '0' else (s if t == '0' else f'{s} + {t}')
    c1 = term(a.get((w0 >> 20) & 15, '0'), b.get((w1 >> 28) & 15, '0'), c.get((w0 >> 15) & 31, '0'), d.get((w1 >> 15) & 7, '0'))
    a1 = term(aa.get((w0 >> 12) & 7), aa.get((w1 >> 12) & 7), ac.get((w0 >> 9) & 7), aa.get((w1 >> 9) & 7))
    c2 = term(a.get((w0 >> 5) & 15, '0'), b.get((w1 >> 24) & 15, '0'), c.get(w0 & 31, '0'), d.get((w1 >> 6) & 7, '0'))
    a2 = term(aa.get((w1 >> 21) & 7), aa.get((w1 >> 3) & 7), ac.get((w1 >> 18) & 7), aa.get(w1 & 7))
    return f'color {c1}, then {c2}; alpha {a1}, then {a2}'


RENDER_MODES = {0xC4112078: 'opaque (the game\'s)', 0xC41049D8: 'translucent, alpha blended (the game\'s cloak)',
                0xC4113078: 'cut-out (texture edge)', 0xC4113878: 'cut-out (texture edge, GE)',
                0x00504130: 'translucent, no fog', 0x00503878: 'translucent glow, no fog',
                0x441049D8: 'translucent (blend with fog color)'}
STATE_PRESETS = [
    # id, label, description, commands
    ('PRIM', 'Prim Color', 'Primitive color: what PRIM means in the combiner (tints, flat colors)',
     [(0xFA000000, 0xFFFFFFFF)]),
    ('ENV', 'Env Color', 'Environment color: ENV in the combiner (the game also uses it for fades)',
     [(0xFB000000, 0xFFFFFFFF)]),
    ('BLEND', 'Blend Color', 'Blend color (its alpha is the alpha compare threshold)', [(0xF9000000, 0x00000008)]),
    ('CC_TEX', 'Combiner: Texture x Shade', 'The game\'s usual: texture lit by the lights',
     [encode_cc(('TEXEL0', '0', 'SHADE', '0'), ('0', '0', '0', 'TEXEL0'),
                ('COMBINED', '0', 'ENV', '0'), ('COMBINED', '0', 'ENV', '0'))]),
    ('CC_TEX_PRIM', 'Combiner: Texture x Shade x Prim', 'Texture tinted by the prim color, lit',
     [encode_cc(('TEXEL0', '0', 'SHADE', '0'), ('0', '0', '0', 'TEXEL0'),
                ('COMBINED', '0', 'PRIM', '0'), ('COMBINED', '0', 'ENV', '0'))]),
    ('CC_PRIM', 'Combiner: Prim x Shade', 'No texture: the prim color, lit',
     [encode_cc(('PRIM', '0', 'SHADE', '0'), ('0', '0', '0', '1'),
                ('COMBINED', '0', 'ENV', '0'), ('COMBINED', '0', 'ENV', '0'))]),
    ('CC_GRADIENT', 'Combiner: Env to Prim by Texture', 'Gray texture as a gradient from the env color (dark) '
     'to the prim color (bright), alpha from the texture x prim alpha (glows, trails)',
     [encode_cc(('PRIM', 'ENV', 'TEXEL0', 'ENV'), ('TEXEL0', '0', 'PRIM', '0'),
                ('PRIM', 'ENV', 'TEXEL0', 'ENV'), ('TEXEL0', '0', 'PRIM', '0'))]),
    ('RM_XLU', 'Render Mode: Translucent', 'Alpha blended (the cloaking device then can\'t fade it: drawn last)',
     [(0xE200001C, 0xC41049D8)]),
    ('RM_NOFOG', 'Render Mode: Translucent, No Fog', 'Alpha blended, ignores the game\'s fog / flash colors',
     [(0xE200001C, 0x00504130)]),
    ('ALPHA_CMP', 'Alpha Compare', 'Pixels with alpha under the blend color alpha aren\'t drawn',
     [(0xF9000000, 0x00000008), (0xE2001E01, 0x00000001)]),
    ('RAW', 'Raw Command', 'Any display list command (two hex words)', []),
]


def parse_state(m):
    out = []
    for part in str(m.get('n64_state', '')).replace(',', ' ').split(';'):
        w = part.split()
        if len(w) == 2:
            try:
                out.append((int(w[0], 16), int(w[1], 16)))
            except ValueError:
                pass
    return out


def write_state(m, cmds):
    m['n64_state'] = '; '.join(f'{a:08X} {b:08X}' for a, b in cmds)
    update_view(m)


def describe(w0, w1):
    op = w0 >> 24
    rgba = f'#{w1 >> 8:06X}, alpha {w1 & 255}'
    if w0 == 0xE200001C:
        return 'Render mode: ' + RENDER_MODES.get(w1, f'custom {w1:08X}')
    if w0 == 0xE2001E01:
        return 'Alpha compare: ' + {0: 'off', 1: 'on (threshold: blend alpha)', 3: 'dither'}.get(w1, str(w1))
    if w0 == 0xE2001300:
        return 'Coverage x alpha (cut-out): ' + ('on' if w1 else 'off')
    if w0 == 0xE3001201:
        return 'Texture filter: ' + {0: 'point', 0x2000: 'bilinear', 0x3000: 'average'}.get(w1, hex(w1))
    if w0 == 0xE3001001:
        return 'Palette mode: ' + {0: 'none', 0x8000: 'RGBA16', 0xC000: 'IA16'}.get(w1, hex(w1))
    if op == 0xFA:
        return 'Prim color ' + rgba
    if op == 0xFB:
        return 'Env color ' + rgba
    if op == 0xF9:
        return 'Blend color ' + rgba
    if op == 0xF8:
        return 'Fog color ' + rgba
    if op == 0xFC:
        return 'Combiner: ' + decode_cc(w0, w1)
    if op == 0xDB:
        return 'Light color ' + rgba
    if op == 0xE2:
        return 'Other mode (low bits)'
    if op == 0xE3:
        return 'Other mode (high bits)'
    return f'Command {op:02X}'


def color_cmd(op, name):
    """Color picker (with alpha) for the first command `op` in n64_state."""
    def get(self):
        for a, b in parse_state(self):
            if a >> 24 == op:
                return [((b >> s) & 255) / 255 for s in (24, 16, 8, 0)]
        return [1.0, 1.0, 1.0, 1.0]

    def set(self, v):
        cmds = parse_state(self)
        val = 0
        for c, sh in zip(v, (24, 16, 8, 0)):
            val |= max(0, min(255, round(c * 255))) << sh
        for i, (a, b) in enumerate(cmds):
            if a >> 24 == op:
                cmds[i] = (a, val)
                break
        else:
            cmds.append((op << 24, val))
        write_state(self, cmds)
    return bpy.props.FloatVectorProperty(subtype='COLOR_GAMMA', size=4, min=0.0, max=1.0, get=get, set=set,
                                         name=name)


class N64_OT_state_add(bpy.types.Operator):
    bl_idname = 'n64.state_add'
    bl_label = 'Add Command'
    bl_description = 'Add a display list command to this material'
    bl_options = {'UNDO'}
    preset: bpy.props.EnumProperty(items=[(p[0], p[1], p[2]) for p in STATE_PRESETS])
    w0: bpy.props.StringProperty(name='Word 0', default='E7000000')
    w1: bpy.props.StringProperty(name='Word 1', default='00000000')

    def invoke(self, context, event):
        if self.preset == 'RAW':
            return context.window_manager.invoke_props_dialog(self)
        return self.execute(context)

    def draw(self, context):
        self.layout.prop(self, 'w0')
        self.layout.prop(self, 'w1')

    def execute(self, context):
        m = context.material
        cmds = parse_state(m)
        if self.preset == 'RAW':
            try:
                new = [(int(self.w0, 16), int(self.w1, 16))]
            except ValueError:
                self.report({'ERROR'}, 'Words are 8 hex digits')
                return {'CANCELLED'}
        else:
            new = next(p[3] for p in STATE_PRESETS if p[0] == self.preset)
        for a, b in new:                      # one of each kind (replace)
            key = a if a >> 24 in (0xE2, 0xE3, 0xDB) else a >> 24
            cmds = [(x, y) for x, y in cmds if (x if x >> 24 in (0xE2, 0xE3, 0xDB) else x >> 24) != key]
            cmds.append((a, b))
        write_state(m, cmds)
        return {'FINISHED'}


class N64_OT_state_edit(bpy.types.Operator):
    bl_idname = 'n64.state_edit'
    bl_label = 'Edit Command'
    bl_description = 'Edit this command\'s two hex words'
    bl_options = {'UNDO'}
    index: bpy.props.IntProperty()
    w0: bpy.props.StringProperty(name='Word 0')
    w1: bpy.props.StringProperty(name='Word 1')

    def invoke(self, context, event):
        cmds = parse_state(context.material)
        self.w0, self.w1 = f'{cmds[self.index][0]:08X}', f'{cmds[self.index][1]:08X}'
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        cmds = parse_state(context.material)
        try:
            cmds[self.index] = (int(self.w0, 16), int(self.w1, 16))
        except (ValueError, IndexError):
            return {'CANCELLED'}
        write_state(context.material, cmds)
        return {'FINISHED'}


class N64_OT_state_remove(bpy.types.Operator):
    bl_idname = 'n64.state_remove'
    bl_label = 'Remove Command'
    bl_description = 'Remove this command'
    bl_options = {'UNDO'}
    index: bpy.props.IntProperty()

    def execute(self, context):
        cmds = parse_state(context.material)
        if 0 <= self.index < len(cmds):
            del cmds[self.index]
        write_state(context.material, cmds)
        return {'FINISHED'}


def _tex_node(m):
    """The material's image texture node (with or without an image)."""
    if m.node_tree is None:
        return None
    return next((n for n in m.node_tree.nodes if n.type == 'TEX_IMAGE' and not n.name.startswith('N64 ')), None)


def _bsdf(m):
    return next((n for n in m.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None) if m.node_tree else None


def _base_get(self):
    b = _bsdf(self)
    c = b.inputs['Base Color'].default_value if b else (0.8, 0.8, 0.8, 1)
    return [linear_to_srgb(x) for x in c[:3]]


def _base_set(self, v):
    b = _bsdf(self)
    if b is None:
        return
    lin = [_srgb_lin(x) for x in v]
    b.inputs['Base Color'].default_value = (*lin, 1.0)
    self.diffuse_color = (*lin, 1.0)
    update_view(self)


def linear_to_srgb(c):
    c = min(max(c, 0.0), 1.0)
    return c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


class N64_OT_texture_add(bpy.types.Operator):
    bl_idname = 'n64.texture_add'
    bl_label = 'Add Texture'
    bl_description = 'Give this material a texture (then open or create its image here)'
    bl_options = {'UNDO'}

    def execute(self, context):
        m = context.material
        if m.node_tree is None:
            m.use_nodes = True
        nt = m.node_tree
        tex = _tex_node(m) or nt.nodes.new('ShaderNodeTexImage')
        bsdf = _bsdf(m)
        if bsdf is not None:
            tex.location = (bsdf.location.x - 600, bsdf.location.y)
        tex.interpolation = 'Linear'
        update_view(m)
        return {'FINISHED'}


class N64_OT_texture_remove(bpy.types.Operator):
    bl_idname = 'n64.texture_remove'
    bl_label = 'Remove Texture'
    bl_description = 'No texture: the material is its Base Color (per costume)'
    bl_options = {'UNDO'}

    def execute(self, context):
        m = context.material
        for n in [n for n in m.node_tree.nodes if n.type == 'TEX_IMAGE' or n.name.startswith('N64 ')]:
            m.node_tree.nodes.remove(n)
        update_view(m)
        return {'FINISHED'}


def _image_changed(*_):
    """An image was picked / opened on a texture node: refresh those materials' N64 view."""
    for m in bpy.data.materials:
        tex = _tex_node(m)
        name = tex.image.name if tex is not None and tex.image is not None else ''
        if m.get('_n64_image', '') != name:
            m['_n64_image'] = name
            update_view(m)


_MSGBUS_OWNER = object()


RENDER_MODE_OPTIONS = [
    ('GAME', 'Game (Opaque)', 'No render mode of its own: the game\'s (opaque, or faded by the cloaking device). '
     'Textures with transparent pixels are cut out', None),
    ('CUTOUT', 'Cut-out', 'Transparent pixels not drawn, hard edges (the build makes it cloaking-safe)', 0xC4113078),
    ('XLU', 'Translucent', 'Alpha blended, with the game\'s fog / flash colors (the cloaking device can\'t fade it)',
     0xC41049D8),
    ('XLU_NOFOG', 'Translucent, No Fog', 'Alpha blended, ignores the game\'s fog / flash colors (effects)', 0x00504130),
    ('GLOW', 'Glow', 'Alpha blended, no fog, depth tested but not written (trails, glows)', 0x00503878),
    ('XLU_FOGCOLOR', 'Translucent over Fog Color', 'Alpha blended against the fog color (flashes)', 0x441049D8),
    ('CUSTOM', 'Custom', 'A render mode value of its own (edit it in the command list)', -1),
]
COMBINER_OPTIONS = [('MODEL', 'Model Default', 'No combiner of its own: texture x shade (the model\'s)', None)] + [
    (p[0], p[1].replace('Combiner: ', ''), p[2], p[3][0]) for p in STATE_PRESETS if p[0].startswith('CC_')] + [
    ('CUSTOM', 'Custom', 'A combiner of its own (edit it in the command list)', -1)]


def command_enum(options, match, value, make, name, description):
    """A dropdown over one command in n64_state: `match(w0)` finds it, `value(w0, w1)` is
    what the options hold (None option = no such command, -1 = custom), `make(v)` builds it."""
    def get(self):
        cur = next((value(a, b) for a, b in parse_state(self) if match(a)), None)
        if cur is None:
            return next((i for i, o in enumerate(options) if o[3] is None), 0)
        return next((i for i, o in enumerate(options) if o[3] == cur), len(options) - 1)

    def set(self, i):
        v = options[i][3]
        if v == -1:                                  # custom: kept as it is
            return
        cmds = [(a, b) for a, b in parse_state(self) if not match(a)]
        if v is not None:
            cmds.append(make(v))
        write_state(self, cmds)
    return bpy.props.EnumProperty(items=[o[:3] for o in options], get=get, set=set, name=name,
                                  description=description)


# ---------------------------------------------------------------- advanced editors
# Every field of the combiner (two cycles; color and alpha: (a - b) * c + d) and of the
# render mode (blender formula per cycle + flags), editing the material's commands.
CC_FIELDS = [   # (prop, word, shift, mask, table)
    ('c1a', 0, 20, 15, CC_A), ('c1b', 1, 28, 15, CC_B), ('c1c', 0, 15, 31, CC_C), ('c1d', 1, 15, 7, CC_D),
    ('a1a', 0, 12, 7, AC_ABD), ('a1b', 1, 12, 7, AC_ABD), ('a1c', 0, 9, 7, AC_C), ('a1d', 1, 9, 7, AC_ABD),
    ('c2a', 0, 5, 15, CC_A), ('c2b', 1, 24, 15, CC_B), ('c2c', 0, 0, 31, CC_C), ('c2d', 1, 6, 7, CC_D),
    ('a2a', 1, 21, 7, AC_ABD), ('a2b', 1, 3, 7, AC_ABD), ('a2c', 1, 18, 7, AC_C), ('a2d', 1, 0, 7, AC_ABD),
]
CC_INPUT_DESC = {
    'COMBINED': 'The first cycle\'s result (in cycle 1: the previous pixel\'s, avoid)',
    'TEXEL0': 'The texture', 'TEXEL1': 'Second texture (the same texture here)',
    'PRIM': 'Prim color', 'SHADE': 'Lighting (or vertex colors when unlit)', 'ENV': 'Env color (the game uses it for fades)',
    '1': 'One', '0': 'Zero', 'NOISE': 'Random noise', 'CENTER': 'Chroma key center', 'K4': 'YUV constant K4',
    'K5': 'YUV constant K5', 'SCALE': 'Chroma key scale', 'COMBINED_ALPHA': 'First cycle\'s alpha',
    'TEXEL0_ALPHA': 'Texture alpha', 'TEXEL1_ALPHA': 'Second texture alpha', 'PRIM_ALPHA': 'Prim alpha',
    'SHADE_ALPHA': 'Lighting / vertex alpha', 'ENV_ALPHA': 'Env alpha (the game\'s fade)',
    'LOD_FRACTION': 'Mipmap level fraction', 'PRIM_LOD_FRAC': 'Prim LOD fraction',
}
DEFAULT_CC = (0xFC127E05, 0xFF17F3FF)


def cc_field(word, shift, mask, table, name):
    items = [(k, k.replace('_', ' ').title() if not k.isdigit() else k, CC_INPUT_DESC.get(k, ''), v)
             for k, v in sorted(table.items(), key=lambda kv: kv[1])]

    def get(self):
        cmds = parse_state(self)
        w = next(((a, b) for a, b in cmds if a >> 24 == 0xFC), DEFAULT_CC)
        v = (w[word] >> shift) & mask
        known = [it[3] for it in items]
        return v if v in known else max(known)          # unnamed values all mean zero

    def set(self, v):
        cmds = parse_state(self)
        i = next((k for k, (a, b) in enumerate(cmds) if a >> 24 == 0xFC), None)
        w = list(cmds[i] if i is not None else DEFAULT_CC)
        w[word] = (w[word] & ~(mask << shift)) | (v << shift)
        w[0] |= 0xFC000000
        if i is None:
            cmds.append(tuple(w))
        else:
            cmds[i] = tuple(w)
        write_state(self, cmds)
    return bpy.props.EnumProperty(items=items, get=get, set=set, name=name)


RM_BLEND_P = [('CLR_IN', 'Pixel Color', 'The combiner\'s color', 0), ('CLR_MEM', 'Framebuffer Color', 'What\'s behind', 1),
              ('CLR_BL', 'Blend Color', 'The blend color', 2), ('CLR_FOG', 'Fog Color', 'The fog color', 3)]
RM_BLEND_A = [('A_IN', 'Pixel Alpha', 'The combiner\'s alpha', 0), ('A_FOG', 'Fog Alpha', 'The fog color\'s alpha', 1),
              ('A_SHADE', 'Shade Alpha', 'Vertex / fog depth alpha', 2), ('0', 'Zero', '', 3)]
RM_BLEND_B = [('1MA', '1 - A', 'One minus the first alpha (a normal blend)', 0),
              ('A_MEM', 'Framebuffer Alpha', 'Coverage stored with what\'s behind', 1), ('1', 'One', '', 2),
              ('0', 'Zero', '', 3)]
RM_FIELDS = [   # (prop, shift, items): blender (P * A + M * B) / (A + B), per cycle
    ('rm1p', 30, RM_BLEND_P), ('rm1a', 26, RM_BLEND_A), ('rm1m', 22, RM_BLEND_P), ('rm1b', 18, RM_BLEND_B),
    ('rm2p', 28, RM_BLEND_P), ('rm2a', 24, RM_BLEND_A), ('rm2m', 20, RM_BLEND_P), ('rm2b', 16, RM_BLEND_B),
]
RM_FLAGS = [   # (prop, bit, label, description)
    ('rm_aa', 0x8, 'Anti-aliasing', 'Smooth edges (coverage)'),
    ('rm_zcmp', 0x10, 'Z Compare', 'Hidden behind what\'s nearer'),
    ('rm_zupd', 0x20, 'Z Update', 'Hides what\'s drawn later behind it (off for most transparent things)'),
    ('rm_imrd', 0x40, 'Read Framebuffer', 'Needed to blend with what\'s behind'),
    ('rm_clroncvg', 0x80, 'Color on Coverage', 'Only write color where coverage wraps (decals)'),
    ('rm_cvgxalpha', 0x1000, 'Coverage x Alpha', 'Texture alpha cuts the pixel coverage (cut-out edges)'),
    ('rm_alphacvgsel', 0x2000, 'Alpha from Coverage', 'Blend with coverage instead of the combiner alpha'),
    ('rm_forcebl', 0x4000, 'Force Blend', 'Always blend (translucency)'),
]
RM_CVG = [('CLAMP', 'Clamp', '', 0), ('WRAP', 'Wrap', '', 1), ('FULL', 'Full', '', 2), ('SAVE', 'Save', '', 3)]
RM_ZMODE = [('OPA', 'Opaque', 'Normal depth', 0), ('INTER', 'Interpenetrating', 'Antialiased intersections', 1),
            ('XLU', 'Translucent', 'For transparent surfaces', 2), ('DEC', 'Decal', 'Drawn on top of a coplanar surface', 3)]
DEFAULT_RM = 0xC4112078          # the game's opaque fighter mode


def _rm_get(self):
    return next((b for a, b in parse_state(self) if a == 0xE200001C), DEFAULT_RM)


def _rm_set(self, value):
    cmds = [(a, b) for a, b in parse_state(self) if a != 0xE200001C] + [(0xE200001C, value & 0xFFFFFFFF)]
    write_state(self, cmds)


def rm_field(shift, mask, items, name, description=''):
    def get(self):
        return (_rm_get(self) >> shift) & mask

    def set(self, v):
        _rm_set(self, (_rm_get(self) & ~(mask << shift)) | (v << shift))
    return bpy.props.EnumProperty(items=items, get=get, set=set, name=name, description=description)


def rm_flag(bit, name, description):
    def get(self):
        return bool(_rm_get(self) & bit)

    def set(self, on):
        _rm_set(self, (_rm_get(self) | bit) if on else (_rm_get(self) & ~bit))
    return bpy.props.BoolProperty(get=get, set=set, name=name, description=description)


ADV_PROPS = {f'n64cc_{n}': cc_field(w, sh, mk, tb, n) for n, w, sh, mk, tb in CC_FIELDS}
ADV_PROPS.update({f'n64rm_{n}': rm_field(sh, 3, it, n) for n, sh, it in RM_FIELDS})
ADV_PROPS.update({f'n64rm_{n}': rm_flag(bit, lab, d) for n, bit, lab, d in RM_FLAGS})
ADV_PROPS['n64rm_cvgdst'] = rm_field(8, 3, RM_CVG, 'Coverage Destination', 'How coverage is stored')
ADV_PROPS['n64rm_zmode'] = rm_field(10, 3, RM_ZMODE, 'Z Mode', 'Depth mode')
ADV_PROPS['n64ui_show_cc'] = bpy.props.BoolProperty(name='Combiner Inputs', description='Every input of the combiner')
ADV_PROPS['n64ui_show_rm'] = bpy.props.BoolProperty(name='Render Mode Settings',
                                                     description='The blender formula and flags of the render mode')


def draw_advanced(lay, m):
    box = lay.box()
    box.prop(m, 'n64ui_show_cc', icon='TRIA_DOWN' if m.n64ui_show_cc else 'TRIA_RIGHT', emboss=False)
    if m.n64ui_show_cc:
        for cyc, title in ((1, 'Cycle 1'), (2, 'Cycle 2 (its input "Combined" = cycle 1)')):
            box.label(text=f'{title}: (A - B) x C + D')
            for kind in ('c', 'a'):
                row = box.row(align=True)
                row.label(text='Color' if kind == 'c' else 'Alpha')
                for f in 'abcd':
                    row.prop(m, f'n64cc_{kind}{cyc}{f}', text='')
    box = lay.box()
    box.prop(m, 'n64ui_show_rm', icon='TRIA_DOWN' if m.n64ui_show_rm else 'TRIA_RIGHT', emboss=False)
    if m.n64ui_show_rm:
        for cyc in (1, 2):
            box.label(text=f'Cycle {cyc} blend: (P x A + M x B) / (A + B)')
            row = box.row(align=True)
            for f in 'pamb':
                row.prop(m, f'n64rm_rm{cyc}{f}', text='')
        col = box.column(align=True)
        for n, _, lab, _ in RM_FLAGS:
            col.prop(m, f'n64rm_{n}', text=lab)
        box.prop(m, 'n64rm_zmode', text='Z Mode')
        box.prop(m, 'n64rm_cvgdst', text='Coverage')
        box.label(text="Cycle 1 is the game's fog; cycle 2 is the material's blend.", icon='INFO')


WRAP_DESC = {'clamp': 'Past the edge, the edge pixels repeat (the texture is shown once)',
             'repeat': 'The texture tiles', 'mirror': 'The texture tiles, every other copy flipped'}
PROPS = {
    'n64ui_format': text_enum(
        'n64_format', FORMATS, 'CI4', name='Format',
        description='How the texture is stored in the game (changing it rebuilds it from the costume images)',
        descriptions={
            'CI4': '16 colors with a palette per costume, 4 bits per pixel (the usual)',
            'CI8': '256 colors with a palette per costume, 8 bits per pixel (half the max size of CI4)',
            'IA4': 'Gray + on/off alpha, 4 bits per pixel (tinted by the combiner)',
            'IA8': 'Gray + alpha, 16 levels each (smoke, glows: alpha blended)',
            'IA16': 'Gray + alpha, 256 levels each',
            'I4': 'Gray only, 16 levels', 'I8': 'Gray only, 256 levels',
            'RGBA16': 'Any colors (32 levels per channel) + on/off alpha, no palette, so the same in every '
                      'costume'}),
    'n64ui_filter': text_enum('n64_filter', ['bilinear', 'point', 'average'], 'bilinear', name='Filtering',
                              description='How the texture is sampled between its pixels',
                              descriptions={'bilinear': 'Smooth (the game\'s default)',
                                            'point': 'Sharp pixels', 'average': 'Box average'}),
    'n64ui_prim': color_cmd(0xFA, 'Prim Color'),
    'n64ui_env': color_cmd(0xFB, 'Env Color'),
    'n64ui_blend': color_cmd(0xF9, 'Blend Color'),
    'n64ui_base': bpy.props.FloatVectorProperty(
        subtype='COLOR_GAMMA', size=3, min=0.0, max=1.0, get=_base_get, set=_base_set, name='Color',
        description='The color of an untextured material (each costume\'s material has its own)'),
    'n64ui_render_mode': command_enum(
        RENDER_MODE_OPTIONS, lambda a: a == 0xE200001C, lambda a, b: b, lambda v: (0xE200001C, v),
        'Render Mode', 'How it\'s blended with what\'s behind it'),
    'n64ui_combiner': command_enum(
        COMBINER_OPTIONS, lambda a: a >> 24 == 0xFC, lambda a, b: (a, b), lambda v: v,
        'Combiner', 'How texture, prim / env colors and lighting are mixed into the final color and alpha'),
    'n64ui_wrap_s': text_enum('n64_wrap_s', WRAPS, 'clamp', descriptions=WRAP_DESC, name='Wrap X',
                              description='What the texture does past its left / right edge (U)'),
    'n64ui_wrap_t': text_enum('n64_wrap_t', WRAPS, 'clamp', descriptions=WRAP_DESC, name='Wrap Y',
                              description='What the texture does past its top / bottom edge (V)'),
    'n64ui_cull': bpy.props.EnumProperty(
        items=[('back', 'Back', 'Faces seen from behind are not drawn (the game\'s default; same as the '
                                'Backface Culling checkbox)'),
               ('none', 'None', 'Double-sided: both sides drawn (thin parts: capes, wings, flat effects)'),
               ('front', 'Front', 'Only faces seen from behind are drawn'),
               ('both', 'Both', 'Not drawn at all')],
        get=_cull_get, set=_cull_set, name='Culling',
        description='Which sides of the faces the game draws (face direction = Blender\'s normals)'),
    'n64ui_lit': bpy.props.BoolProperty(
        get=_lit_get, set=_lit_set, name='Lit',
        description='On: shaded by the light and shadow colors. Off: shaded by the vertex colors (Color '
                    'Attribute) instead, like glows and effects'),
    'n64ui_own_light': own_flag('n64_light_color', 'FFFFFF', name='Own Light Color',
                                description='Give this material its own light color instead of the model\'s '
                                            '(Scene > N64 Model)'),
    'n64ui_light': hex_color('n64_light_color', 'FFFFFF', name='Light Color',
                             description='Color of the lit side'),
    'n64ui_own_shadow': own_flag('n64_shadow_color', '808080', name='Own Shadow Color',
                                 description='Give this material its own shadow color instead of the model\'s '
                                             '(e.g. a face that should never get dark)'),
    'n64ui_shadow': hex_color('n64_shadow_color', '808080', name='Shadow Color',
                              description='Color of the side facing away from the light (ambient)'),
}
SCENE_PROPS = {
    'n64ui_light': hex_color('n64_light_color', 'FFFFFF', name='Light Color',
                             description='The model\'s light color: the lit side of every material '
                                         '(unless it has its own)'),
    'n64ui_shadow': hex_color('n64_shadow_color', '808080', name='Shadow Color',
                              description='The model\'s shadow (ambient) color: the side facing away from '
                                          'the light'),
}


class N64_PT_material(bpy.types.Panel):
    bl_label = 'N64 Model'
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'material'
    # first in the Material tab: header-less panels (like Blender's material slots) always
    # come before the others; among them, the lowest order first
    bl_order = -100
    bl_options = {'HIDE_HEADER'}

    @classmethod
    def poll(cls, context):
        if context.object is not None and context.object.get('n64_gamedata'):
            return False                   # (hurtboxes, collision: Object > N64 Game Data)
        return context.object is not None or context.material is not None

    def draw(self, context):
        lay = self.layout
        lay.label(text='N64 Model', icon='MATERIAL')
        ob = context.object
        if ob is not None and ob.type == 'MESH':             # which material (the object's slots)
            row = lay.row()
            row.template_list('MATERIAL_UL_matslots', 'n64', ob, 'material_slots', ob, 'active_material_index',
                              rows=2)
            lay.template_ID(ob, 'active_material', new='material.new')
        m = context.material
        if m is None:
            return
        tex = _tex_node(m)
        box = lay.box()
        if tex is not None:
            row = box.row()
            row.label(text='Texture', icon='TEXTURE')
            row.operator('n64.texture_remove', text='', icon='X')
            box.template_ID(tex, 'image', new='image.new', open='image.open')
            if tex.image is not None:
                box.label(text=f'{tex.image.size[0]} x {tex.image.size[1]}  (one image per costume material)')
        else:
            row = box.row()
            row.prop(m, 'n64ui_base', text='Color')
            row.operator('n64.texture_add', text='Add Texture', icon='TEXTURE')
        lay.use_property_split = True
        textured = tex is not None and tex.image is not None
        col = lay.column()
        if textured:
            col.prop(m, 'n64ui_format', text='Format')
            col.prop(m, 'n64ui_wrap_s', text='Wrap X')
            col.prop(m, 'n64ui_wrap_t', text='Wrap Y')
            col.prop(m, 'n64ui_filter', text='Filtering')
        else:
            col.label(text='No texture: the Base Color is the color', icon='INFO')
        col.prop(m, 'n64ui_render_mode', text='Render Mode')
        col.prop(m, 'n64ui_combiner', text='Combiner')
        if any(a >> 24 == 0xFA for a, _ in parse_state(m)) or m.n64ui_combiner in ('CC_TEX_PRIM', 'CC_GRADIENT'):
            col.prop(m, 'n64ui_prim', text='Prim Color')
        if any(a >> 24 == 0xFB for a, _ in parse_state(m)) or m.n64ui_combiner == 'CC_GRADIENT':
            col.prop(m, 'n64ui_env', text='Env Color')
        draw_advanced(col, m)
        col.prop(m, 'n64ui_cull', text='Culling')
        col.prop(m, 'n64ui_lit', text='Lit')
        row = col.row(heading='Own Light')
        row.prop(m, 'n64ui_own_light', text='')
        sub = row.row()
        sub.enabled = m.n64ui_own_light
        sub.prop(m, 'n64ui_light', text='')
        row = col.row(heading='Own Shadow')
        row.prop(m, 'n64ui_own_shadow', text='')
        sub = row.row()
        sub.enabled = m.n64ui_own_shadow
        sub.prop(m, 'n64ui_shadow', text='')
        box = lay.box()
        head = box.row()
        head.label(text='Display List Commands', icon='SCRIPT')
        head.operator_menu_enum('n64.state_add', 'preset', text='Add', icon='ADD')
        cmds = parse_state(m)
        if not cmds:
            box.label(text='None (the model\'s defaults)')
        for i, (a, b) in enumerate(cmds):
            row = box.row(align=True)
            sub = row.column()
            sub.label(text=describe(a, b))
            sub.label(text=f'    {a:08X} {b:08X}')
            if a >> 24 in (0xFA, 0xFB, 0xF9):
                row.prop(m, {0xFA: 'n64ui_prim', 0xFB: 'n64ui_env', 0xF9: 'n64ui_blend'}[a >> 24], text='')
            op = row.operator('n64.state_edit', text='', icon='GREASEPENCIL')
            op.index = i
            op = row.operator('n64.state_remove', text='', icon='X')
            op.index = i
        draw_anim(lay, m, context)


def draw_anim(lay, m, context):
    """Material animation (stages / items): the animatable properties, loop, image list."""
    scn = context.scene
    props = anim_props(m)
    frames = frame_nodes(m)
    if not props and not frames and not (scn and scn.get('n64_kind')):
        return
    box = lay.box()
    head = box.row()
    head.label(text='Material Animation', icon='ANIM')
    head.operator_menu_enum('n64.anim_add', 'prop', text='Add', icon='ADD')
    animated = anim_animated(m)
    if not props:
        box.label(text='None: add a property, then keyframe it (hover it + I)')
    col = box.column()
    col.use_property_split = True
    for p in props:
        row = col.row(align=True)
        row.prop(m, f'["{p}"]', text=ANIM_INFO[p][1])
        op = row.operator('n64.anim_remove', text='', icon='X')
        op.prop = p
    if animated:
        col.separator()
        if 'n64_anim_frames' in m.keys():
            col.prop(m, '["n64_anim_frames"]', text='Length (frames)')
        if 'n64_anim_loop' in m.keys():
            col.prop(m, 'n64ui_anim_loop', text='Loop')
        if m.get('n64_anim_loop') and 'n64_anim_loop_start' in m.keys():
            col.prop(m, '["n64_anim_loop_start"]', text='Loop Start')
        if 'n64_anim_frames' not in m.keys():
            col.operator('n64.anim_settings', text='Set Length / Loop', icon='PREVIEW_RANGE')
        box.label(text='60 frames per second, like the game', icon='INFO')
    tex = _tex_node(m)
    if tex is not None:
        sub = box.box()
        row = sub.row()
        row.label(text=f'Images ({len(frames)})', icon='IMAGE_DATA')
        row.operator('n64.frame_add', text='Add Image', icon='ADD').kind = 'Frame'
        for i, n in enumerate(frames):
            r = sub.row(align=True)
            r.label(text=f'{i}')
            r.template_ID(n, 'image', open='image.open')
            op = r.operator('n64.frame_remove', text='', icon='X')
            op.index, op.kind = i, 'Frame'
        if frames and 'n64_tex_index' not in m.keys():
            sub.label(text='Add the Image property to pick / animate which one is drawn')
        if 'n64_tex_index' in animated and len(frames) < 2:
            sub.label(text='The Image property is animated: add the images it picks', icon='ERROR')
        if str(m.get('n64_format', 'CI4')).startswith('CI'):     # palette formats: a palette list
            pals = frame_nodes(m, 'Palette')
            sub = box.box()
            row = sub.row()
            row.label(text=f'Palettes ({len(pals)})', icon='COLOR')
            row.operator('n64.frame_add', text='Add Palette', icon='ADD').kind = 'Palette'
            for i, n in enumerate(pals):
                r = sub.row(align=True)
                r.label(text=f'{i}')
                r.template_ID(n, 'image', open='image.open')
                op = r.operator('n64.frame_remove', text='', icon='X')
                op.index, op.kind = i, 'Palette'
            if pals:
                sub.label(text='Each is the texture in that palette\'s colors: recolor it (same drawing)')
            if 'n64_pal_index' in animated and len(pals) < 2:
                sub.label(text='The Palette property is animated: add the palettes it picks', icon='ERROR')


def _anim_add_items(self, context):
    m = context.material
    have = set(anim_props(m)) if m else set()
    return [(p, label, desc) for p, label, kind, d, desc in ANIM_PROPS if p not in have] or \
        [('NONE', 'All added', '')]


class N64_OT_anim_add(bpy.types.Operator):
    bl_idname = 'n64.anim_add'
    bl_label = 'Add Animated Property'
    bl_description = 'A material property the game can animate (scrolling, image / palette flips, colors)'
    bl_options = {'UNDO'}
    prop: bpy.props.EnumProperty(items=_anim_add_items, name='Property')

    def execute(self, context):
        m = context.material
        if self.prop == 'NONE':
            return {'CANCELLED'}
        anim_prop_create(m, self.prop)
        if self.prop.startswith(('n64_offset', 'n64_scale')):     # the offset pair goes together
            for p in ('n64_offset_x', 'n64_offset_y', 'n64_scale_x', 'n64_scale_y'):
                anim_prop_create(m, p)
            info = dict(m.get('n64_mobj') or {})
            tex = _image_node(m)
            size = list(tex.image.size) if tex is not None and tex.image is not None else [32, 32]
            if not info:
                m['n64_mobj'] = dict(flags=0x20, mode=0, unk0a=0, size0=size, size1=size, tex_size=size)
            elif not int(info.get('flags', 0)) & 0x20:
                info['flags'] = int(info.get('flags', 0)) | 0x20
                m['n64_mobj'] = info
        for prop, kind in (('n64_tex_index', 'Frame'), ('n64_pal_index', 'Palette')):
            if self.prop == prop and not frame_nodes(m, kind):
                tex = _tex_node(m)
                if tex is not None and tex.image is not None:        # its texture is entry 0
                    frame_node(m, kind, 0).image = tex.image
        update_view(m)
        return {'FINISHED'}


class N64_OT_anim_remove(bpy.types.Operator):
    bl_idname = 'n64.anim_remove'
    bl_label = 'Remove Animated Property'
    bl_description = 'Remove this property and its keyframes'
    bl_options = {'UNDO'}
    prop: bpy.props.StringProperty()

    def execute(self, context):
        m = context.material
        fcs = anim_fcurves(m)
        for fc in [fc for fc in (fcs or []) if fc.data_path == f'["{self.prop}"]']:
            fcs.remove(fc)
        if self.prop in m.keys():
            del m[self.prop]
        update_view(m)
        return {'FINISHED'}


class N64_OT_anim_settings(bpy.types.Operator):
    bl_idname = 'n64.anim_settings'
    bl_label = 'Animation Length'
    bl_description = 'How long the material animation is, and whether it loops'
    bl_options = {'UNDO'}

    def execute(self, context):
        m = context.material
        last = max((kp.co.x for fcs in anim_animated(m).values() for fc in fcs for kp in fc.keyframe_points),
                   default=60)
        m['n64_anim_frames'] = max(1, int(round(last)))
        m['n64_anim_loop_start'] = 0
        _mat_loop_set(m, True)
        return {'FINISHED'}


class N64_OT_frame_add(bpy.types.Operator):
    bl_idname = 'n64.frame_add'
    bl_label = 'Add Image'
    bl_description = ('Another entry in the material\'s image list (the Image property picks which is drawn) or '
                      'palette list (a copy of the texture: recolor it, the build reads the palette from it)')
    bl_options = {'UNDO'}
    kind: bpy.props.EnumProperty(items=[('Frame', 'Image', ''), ('Palette', 'Palette', '')], default='Frame')

    def execute(self, context):
        m = context.material
        frames = frame_nodes(m, self.kind)
        tex = _tex_node(m)
        if not frames and tex is not None and tex.image is not None:     # its texture is entry 0
            frame_node(m, self.kind, 0).image = tex.image
            frames = frame_nodes(m, self.kind)
        n = frame_node(m, self.kind, len(frames))
        src = frames[0].image if frames and frames[0].image is not None else (tex.image if tex else None)
        if src is not None:                                       # same size: paint / recolor a copy
            n.image = new_frame_image(src, f'{m.name}_{"frame" if self.kind == "Frame" else "palette"}{len(frames)}')
        anim_prop_create(m, 'n64_tex_index' if self.kind == 'Frame' else 'n64_pal_index')
        update_view(m)
        return {'FINISHED'}


def new_frame_image(src, name):
    """A copy of image src as its own PNG in the .blend's textures folder (what src shows:
    its file copied, or its pixels when it has none / is painted), loaded as a new image."""
    import shutil
    rel = f'//textures/{name}.png'
    k = 1
    while os.path.exists(bpy.path.abspath(rel)):
        k += 1
        rel = f'//textures/{name}_{k}.png'
    path = bpy.path.abspath(rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    src_path = bpy.path.abspath(src.filepath, library=src.library) if src.filepath else ''
    if src_path and os.path.exists(src_path) and not src.is_dirty and src.packed_file is None:
        shutil.copyfile(src_path, path)
    else:
        w, h = src.size
        tmp = bpy.data.images.new(name, w, h, alpha=True)
        tmp.pixels.foreach_set(src.pixels[:])
        tmp.filepath_raw, tmp.file_format = path, 'PNG'
        tmp.save()
        bpy.data.images.remove(tmp)
    img = bpy.data.images.load(path)
    img.name = name
    img.filepath = rel
    return img


def _use_file(img):
    """A list entry's image reads its PNG, not a packed copy (Image.copy() packs the source's
    pixels: the .blend would keep showing those however the PNG is edited). What it shows is
    written to the PNG first."""
    if img is None or img.packed_file is None or not img.filepath:
        return
    try:
        img.save()
        img.unpack(method='REMOVE')
        img.reload()
    except (RuntimeError, OSError):
        pass


class N64_OT_frame_remove(bpy.types.Operator):
    bl_idname = 'n64.frame_remove'
    bl_label = 'Remove Image'
    bl_description = 'Remove this image from the list (the later ones move up)'
    bl_options = {'UNDO'}
    index: bpy.props.IntProperty()
    kind: bpy.props.StringProperty(default='Frame')

    def execute(self, context):
        m = context.material
        frames = frame_nodes(m, self.kind)
        if 0 <= self.index < len(frames):
            m.node_tree.nodes.remove(frames[self.index])
            for i, n in enumerate(frame_nodes(m, self.kind)):
                n.name = n.label = f'N64 {self.kind} {i}'
        update_view(m)
        return {'FINISHED'}


# ---------------------------------------------------------------- joint animation (stages)
# A stage layer's joints (bones L<n>_joint_NN) each play their own script: its length
# (n64_anim_frames), loop (n64_anim_loop: the curves repeat, Cycles) and loop start, as pose
# bone properties. blend_to_gltf.py samples the bone over that length; the import writes the
# script (the original one where nothing changed).
def _bone_fcurves(arm, name):
    fcs = anim_fcurves(arm)
    return [fc for fc in (fcs or []) if fc.data_path.startswith(f'pose.bones["{name}"]')]


def _sync_cycles(fcurves, on):
    for fc in fcurves:
        has = [md for md in fc.modifiers if md.type == 'CYCLES']
        if on and not has:
            fc.modifiers.new('CYCLES')
        elif not on:
            for md in has:
                fc.modifiers.remove(md)
        fc.update()


def _mat_loop_get(self):
    return bool(self.get('n64_anim_loop', False))


def _mat_loop_set(self, on):
    self['n64_anim_loop'] = bool(on)
    _sync_cycles([fc for fc in (anim_fcurves(self) or []) if fc.data_path[2:-2] in ANIM_INFO], on)


def _bone_loop_get(self):
    return bool(self.get('n64_anim_loop', False))


def _bone_loop_set(self, on):
    self['n64_anim_loop'] = bool(on)
    _sync_cycles(_bone_fcurves(self.id_data, self.name), on)


def sync_loops(*_):
    """Every curve of a looping joint / material repeats (Cycles), a non-looping one's doesn't:
    also curves keyed later (Insert Keyframe adds location / scale curves), so Blender always
    plays what the game will. Runs after every change (depsgraph update) that touches an
    action; cheap otherwise."""
    for ob in bpy.data.objects:
        if ob.type != 'ARMATURE' or ob.animation_data is None:
            continue
        fcs = anim_fcurves(ob)
        if not fcs:
            continue
        for fc in fcs:
            if not fc.data_path.startswith('pose.bones["'):
                continue
            pb = ob.pose.bones.get(fc.data_path[len('pose.bones["'):].split('"')[0])
            if pb is None or 'n64_anim_loop' not in pb.keys():
                continue
            has = any(md.type == 'CYCLES' for md in fc.modifiers)
            if bool(pb['n64_anim_loop']) != has:
                _sync_cycles([fc], bool(pb['n64_anim_loop']))
    for m in bpy.data.materials:
        if 'n64_anim_loop' not in m.keys() or m.animation_data is None:
            continue
        for fc in (anim_fcurves(m) or []):
            if fc.data_path[2:-2] in ANIM_INFO and any(md.type == 'CYCLES' for md in fc.modifiers) != bool(m['n64_anim_loop']):
                _sync_cycles([fc], bool(m['n64_anim_loop']))


_SHOWN_ACTION = {}


def _follow_action_range(scene):
    """An armature given another action (Action Editor): the scene's frame range becomes that
    action's (its Manual Frame Range: a fighter animation's length, the shield's 0..360)."""
    for ob in scene.objects:
        if ob.type != 'ARMATURE' or ob.animation_data is None:
            continue
        act = ob.animation_data.action
        name = act.name if act is not None else None
        if _SHOWN_ACTION.get(ob.name) == name:
            continue
        _SHOWN_ACTION[ob.name] = name
        if act is not None and getattr(act, 'use_frame_range', False):
            scene.frame_start, scene.frame_end = int(act.frame_start), int(act.frame_end)
            if not scene.frame_start <= scene.frame_current <= scene.frame_end:
                scene.frame_current = scene.frame_start


def _sync_loops_on_update(scene, depsgraph):
    if depsgraph.id_type_updated('ACTION') or depsgraph.id_type_updated('OBJECT'):
        sync_loops()
        _follow_action_range(scene)


def _last_key(fcurves):
    return max((kp.co.x for fc in fcurves for kp in fc.keyframe_points), default=0.0)


class N64_OT_joint_anim_set(bpy.types.Operator):
    bl_idname = 'n64.joint_anim_set'
    bl_label = 'Game Animation From Keys'
    bl_description = ('Make this joint\'s keyframes its game animation: its length = the last key, looping '
                      '(change them below)')
    bl_options = {'UNDO'}

    def execute(self, context):
        pb = context.active_pose_bone
        fcs = _bone_fcurves(pb.id_data, pb.name)
        pb['n64_anim_frames'] = max(1, int(round(_last_key(fcs))))
        pb['n64_anim_loop_start'] = 0
        _bone_loop_set(pb, True)
        return {'FINISHED'}


class N64_OT_joint_anim_remove(bpy.types.Operator):
    bl_idname = 'n64.joint_anim_remove'
    bl_label = 'Remove Joint Animation'
    bl_description = 'This joint stops animating in the game: its keyframes and settings are removed'
    bl_options = {'UNDO'}

    def execute(self, context):
        pb = context.active_pose_bone
        fcs = anim_fcurves(pb.id_data)
        for fc in _bone_fcurves(pb.id_data, pb.name):
            fcs.remove(fc)
        for k in ('n64_anim_frames', 'n64_anim_loop', 'n64_anim_loop_start', 'n64_anim_locked'):
            if k in pb.keys():
                del pb[k]
        pb.matrix_basis.identity()
        return {'FINISHED'}


class N64_PT_joint(bpy.types.Panel):
    bl_label = 'N64 Joint Animation'
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'bone'

    @classmethod
    def poll(cls, context):
        # stage layers' joints (L<n>_joint_NN), items' / projectiles' (joint_NN: their exports
        # have the scene's n64_kind; a fighter's bones aren't animated this way)
        import re
        pb = context.active_pose_bone
        if pb is None:
            return False
        if re.match(r'L\d+_joint_\d+$', pb.name):
            return True
        return bool(re.match(r'joint_\d+$', pb.name)) and bool(context.scene.get('n64_kind'))

    def draw(self, context):
        lay = self.layout
        pb = context.active_pose_bone
        fcs = _bone_fcurves(pb.id_data, pb.name)
        lay.use_property_split = True
        if 'n64_anim_frames' not in pb.keys():
            if fcs:
                lay.label(text='Keyframed, but not a game animation yet', icon='INFO')
                lay.operator('n64.joint_anim_set', icon='ANIM')
            else:
                lay.label(text='Not animated: keyframe the bone (I), then set it here')
            return
        if pb.get('n64_anim_locked'):
            lay.label(text='Moves along a path (TraI): kept as the original', icon='LOCKED')
        col = lay.column()
        col.prop(pb, '["n64_anim_frames"]', text='Length (frames)')
        col.prop(pb, 'n64ui_anim_loop', text='Loop')
        if pb.get('n64_anim_loop'):
            col.prop(pb, '["n64_anim_loop_start"]', text='Loop Start')
        if fcs and pb.get('n64_anim_loop') and int(round(_last_key(fcs))) != int(pb['n64_anim_frames']):
            lay.label(text=f'Keys end at {_last_key(fcs):g}: Blender repeats them there, the game at '
                           f'the length', icon='ERROR')
        lay.label(text='60 frames per second; each joint loops on its own', icon='INFO')
        lay.operator('n64.joint_anim_remove', icon='X')


# ---------------------------------------------------------------- fighter animation flags
# FTMotionDesc anim_desc (decomp ftdef.h / ftmain.c ftMainSetStatus): bit 31 - i turns the
# fighter's hidden part i on (attr->hiddenparts: a joint added to its tree, e.g. TransN, XRotN,
# YRotN); the low bits are modes
MODE_FLAGS = (
    (0x10, 'Submotion Script', 'Its command script is in the submotion file instead of the mainmotion one'),
    (0x08, '32-bit Scripts', 'The animation uses the 32-bit script format (like stage animations) '
                             'instead of the fighters\' 16-bit one'),
    (0x04, 'No Translate Scales', 'Joint translations aren\'t scaled by the fighter\'s translate_scales '
                                  '(only matters for fighters that have them)'),
    (0x02, 'Shield Pose File', 'The animation is read from the shield-pose file (guard on / off)'),
    (0x01, 'Animation Locks', 'The fighter\'s locked joints (attributes animlock) are animated too'),
)
HIDDEN_DESC = {
    'TransN': 'Root motion: the game moves the fighter by its translation',
    'XRotN': 'A joint above the body that turns all of it',
    'YRotN': 'XRotN\'s last child: where the shield sits',
}


def _action_flags(act):
    try:
        return int(str(act.get('n64_flags', '0')), 16) & 0xFFFFFFFF
    except ValueError:
        return 0


def _flag_bits_get(self):
    v = _action_flags(self)
    return [bool((v >> i) & 1) for i in range(32)]


def _flag_bits_set(self, bits):
    self['n64_flags'] = f'{sum(1 << i for i, b in enumerate(bits) if b):08X}'


def _flags_hex_get(self):
    return f'{_action_flags(self):08X}'


def _flags_hex_set(self, text):
    try:
        self['n64_flags'] = f'{int(text.strip().lower().removeprefix("0x"), 16) & 0xFFFFFFFF:08X}'
    except ValueError:
        pass


def _anim_loop_get(self):
    return bool(self.get('n64_loop', False))


def _anim_loop_set(self, on):
    self['n64_loop'] = bool(on)


def _flags_owner(context):
    """The armature and its action with motion flags (the active object, or the scene's fighter)."""
    ob = context.active_object
    if ob is None or ob.type != 'ARMATURE':
        ob = next((o for o in context.scene.objects if o.type == 'ARMATURE' and 'n64_hidden_parts' in o.keys()), None)
    if ob is None or ob.animation_data is None or ob.animation_data.action is None:
        return ob, None
    act = ob.animation_data.action
    return ob, (act if 'n64_flags' in act.keys() else None)


def draw_motion_flags(lay, context):
    ob, act = _flags_owner(context)
    if act is None:
        lay.label(text='Pick a fighter animation in the Action Editor', icon='INFO')
        return
    lay.label(text=act.name, icon='ACTION')
    if act.get('n64_actions'):
        col = lay.column(align=True)
        col.label(text='Played by:')
        col.label(text='   ' + str(act['n64_actions']))
    lay.prop(act, 'n64ui_anim_loop', text='Loop')
    row = lay.row()
    row.prop(act, 'n64ui_flags_hex', text='Flags 0x')
    if act.get('n64_flags_guessed'):
        lay.label(text='Guessed from the file\'s joint count', icon='QUESTION')
    v = _action_flags(act)
    parts = list(ob.get('n64_hidden_parts') or []) if ob is not None else []
    box = lay.box()
    box.label(text='Joints added (hidden parts)', icon='BONE_DATA')
    col = box.column(align=True)
    parents = list(ob.get('n64_hidden_part_parents') or []) if ob is not None else []
    for i, name in enumerate(parts[:27]):
        bit = 31 - i
        r = col.row()
        r.prop(act, 'n64ui_flag_bits', index=bit, text=f'{name}')
        r.label(text=f'{1 << bit:08X}')
        # a part hanging from another hidden part needs that one on (the game adds it under it)
        par = parents[i] if i < len(parents) else None
        if (v >> bit) & 1 and par in parts and not (v >> (31 - parts.index(par))) & 1:
            col.label(text=f'{name} hangs from {par}: turn {par} on too (the game needs it)', icon='ERROR')
    for bit in range(5, 32):                          # set bits the fighter has no part for
        i = 31 - bit
        if i >= len(parts) and (v >> bit) & 1:
            r = col.row()
            r.prop(act, 'n64ui_flag_bits', index=bit, text=f'Hidden part {i} (none)')
            r.label(text=f'{1 << bit:08X}')
    box.label(text='They set the joints the file\'s scripts drive, in order', icon='INFO')
    box = lay.box()
    box.label(text='Modes', icon='PREFERENCES')
    col = box.column(align=True)
    for bit_val, label, _ in MODE_FLAGS:
        bit = bit_val.bit_length() - 1
        r = col.row()
        r.prop(act, 'n64ui_flag_bits', index=bit, text=label)
        r.label(text=f'{bit_val:08X}')
    if act.get('n64_source'):
        lay.label(text=f"From: {act['n64_source']}")


class N64_PT_motion_flags(bpy.types.Panel):
    """The active fighter animation's motion flags (Properties > Object)."""
    bl_label = 'N64 Animation'
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'object'

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == 'ARMATURE' and 'n64_hidden_parts' in ob.keys()

    def draw(self, context):
        draw_motion_flags(self.layout, context)


def _is_n64_fighter(context):
    return any(o.type == 'ARMATURE' and 'n64_hidden_parts' in o.keys() for o in context.scene.objects)


def _dopesheet_n64_draw(self, context):
    """N64 Animation, first in the Action Editor's sidebar: drawn at the top of Blender's Action
    panel (the sidebar keeps its saved panel order, so a panel of its own would come last)."""
    if not _is_n64_fighter(context):
        return
    box = self.layout.box()
    box.label(text='N64 Animation', icon='ARMATURE_DATA')
    draw_motion_flags(box, context)


def _remove_dopesheet_draws():
    """Every N64 Animation draw added to the Action panel, by any copy of this script (the
    add-on and the one embedded in the .blend, or an earlier file's): only one is drawn."""
    cls = getattr(bpy.types, 'DOPESHEET_PT_action', None)
    if cls is None or not hasattr(cls, '_dyn_ui_initialize'):
        return
    funcs = cls._dyn_ui_initialize()
    for fn in [fn for fn in funcs if getattr(fn, '__name__', '') == '_dopesheet_n64_draw']:
        funcs.remove(fn)


_ASSET_POLL = []


def _hide_pose_asset_panel():
    """The Action tab's Create Pose Asset panel isn't used here: hidden in N64 fighter files."""
    cls = getattr(bpy.types, 'DOPESHEET_PT_asset_panel', None)
    if cls is None or _ASSET_POLL:
        return
    orig = cls.__dict__.get('poll')
    _ASSET_POLL.append((cls, orig))

    def poll(c, context):
        if _is_n64_fighter(context):
            return False
        return orig.__func__(c, context) if orig is not None else True
    cls.poll = classmethod(poll)


def _restore_pose_asset_panel():
    for cls, orig in _ASSET_POLL:
        if orig is not None:
            cls.poll = orig
        else:
            del cls.poll
    _ASSET_POLL.clear()


class N64_PT_scene(bpy.types.Panel):
    bl_label = 'N64 Model'
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'scene'

    def draw(self, context):
        lay = self.layout
        lay.use_property_split = True
        lay.prop(context.scene, 'n64ui_light', text='Light Color')
        lay.prop(context.scene, 'n64ui_shadow', text='Shadow Color')
        if 'n64_light_angle' in context.scene.keys():
            lay.prop(context.scene, '["n64_light_angle"]', text='Light Angle')


# ---------------------------------------------------------------------------- game data objects
# hurtboxes / collision lines / map objects made by build_blend.py (blend_gamedata.py); these
# edit their custom properties, which blend_to_gltf.py reads

HB_PLACEMENTS = [('low', 'Low', 'Legs: hit here, the fighter reacts as hit low'),
                 ('mid', 'Mid', 'Torso, arms'), ('high', 'High', 'Head')]
HB_COLORS = {'low': (0.2, 0.45, 1.0, 0.3), 'mid': (1.0, 0.85, 0.1, 0.3), 'high': (1.0, 0.2, 0.2, 0.3)}
LINE_KINDS = [('floor', 'Floor', 'Stood on (from above)'), ('ceil', 'Ceiling', 'Bumped into from below'),
              ('rwall', 'Right Wall', 'A wall facing right (its solid side to the left: hit moving left)'),
              ('lwall', 'Left Wall', 'A wall facing left (its solid side to the right: hit moving right)')]
FLAG_LEDGE, FLAG_DROP = 0x8000, 0x4000
LINE_COLORS = {'floor': (0.1, 0.85, 0.2, 1), 'ceil': (0.9, 0.15, 0.15, 1),
               'rwall': (0.15, 0.4, 1.0, 1), 'lwall': (1.0, 0.55, 0.1, 1)}
LINE_RADIUS, LINE_VERTEX_RADIUS = 48.0, 72.0    # game units: the line tube and vertex dots
LINE_PIXEL_WIDTH, VERTEX_PIXEL_RADIUS, LEDGE_PIXEL_RADIUS = 6.0, 3.5, 9.0
COLLISION_DRAW_HANDLER = 'n64_collision_draw_handler'
COLL_EDGE_TYPE = 'n64_collision_type'
COLL_EDGE_FLAGS = 'n64_collision_flags'
COLL_EDGE_ORDER = 'n64_collision_line_order'
COLL_EDGE_SEGMENT = 'n64_collision_segment'
COLL_EDGE_START_SIDE = 'n64_collision_start_side'
COLL_EDGE_END_FLAGS = 'n64_collision_end_flags'
COLL_EDGE_POINT_FLAGS = 'n64_collision_has_point_flags'
MAPOBJ_KINDS = {
    0x00: 'P1 start', 0x01: 'P2 start', 0x02: 'P3 start', 0x03: 'P4 start', 0x04: 'item spawn',
    0x05: 'scale L', 0x06: 'scale R', 0x07: 'pakkun L', 0x08: 'pakkun R', 0x09: 'power block',
    0x0A: 'pipe L', 0x0B: 'pipe R', 0x0C: 'acid', 0x0D: 'twister', 0x0E: 'monster', 0x13: 'bumper',
    0x14: 'pipe wall', 0x15: 'movie player 1', 0x16: 'movie player 2', 0x17: 'movie player 3',
    0x18: 'demo P1', 0x19: 'demo P2', 0x1A: 'demo P3', 0x1B: 'demo P4', 0x1C: 'demo P5', 0x1D: 'demo P6',
    0x1E: 'demo P7', 0x1F: 'demo P8', 0x20: 'rebirth platform', 0x21: '1P player', 0x22: '1P ally 1',
    0x23: '1P ally 2', 0x24: '1P ally 3', 0x25: '1P enemy 1', 0x26: '1P enemy 2', 0x27: '1P enemy 3',
    0x28: '1P enemy 4', 0x29: 'bonus3 taru bomb', 0x2B: '1P enemy team', 0x2C: 'challenger player'}


def _helper_mat(name, rgba):
    m = bpy.data.materials.get(name)
    if m is None:
        m = bpy.data.materials.new(name)
        m['n64_helper'] = True
        m.diffuse_color = rgba
        m.use_nodes = True
        bsdf = next((n for n in m.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
        if bsdf is not None:
            bsdf.inputs['Base Color'].default_value = rgba
            bsdf.inputs['Alpha'].default_value = rgba[3]
        if rgba[3] < 1:
            for attr, val in (('surface_render_method', 'BLENDED'), ('blend_method', 'BLEND')):
                try:
                    setattr(m, attr, val)
                except (AttributeError, TypeError):
                    pass
    return m


def _set_mat(ob, m):
    if ob.data is None:
        return
    if ob.data.materials:
        ob.data.materials[0] = m
    else:
        ob.data.materials.append(m)


def _refresh_look(ob):
    kind = ob.get('n64_gamedata')
    if kind == 'hurtbox':
        p = ob.get('n64_hurtbox_placement', 'mid')
        _set_mat(ob, _helper_mat(f'N64 Hurtbox {p}', HB_COLORS.get(p, HB_COLORS['mid'])))
        ob.name = f'hurtbox {ob.parent_bone or "joint_%02d" % ob.get("n64_hurtbox_joint", 0)} {p}'
    elif kind == 'collision':
        t = ob.get('n64_coll_type', 'floor')
        r, g, b, a = LINE_COLORS.get(t, LINE_COLORS['floor'])
        drop = bool(ob.get('n64_drop_through'))
        if drop:
            r, g, b = (r + 1) / 2, (g + 1) / 2, (b + 1) / 2
        _set_mat(ob, _helper_mat(f'N64 Collision {t}' + (' drop-through' if drop else ''), (r, g, b, a)))
        m = re.match(r'(floor|ceil|rwall|lwall)\b(.*)$', ob.name)
        if m and m.group(1) != t:
            ob.name = t + m.group(2)


def obj_enum(key, items, default, name='', description=''):
    ids = [i[0] for i in items]

    def get(self):
        v = self.get(key, default)
        return ids.index(v) if v in ids else ids.index(default)

    def set(self, value):
        self[key] = ids[value]
        _refresh_look(self)
    return bpy.props.EnumProperty(items=items, get=get, set=set, name=name, description=description)


def obj_bool(key, name='', description=''):
    def get(self):
        return bool(self.get(key, False))

    def set(self, value):
        self[key] = bool(value)
        if key == 'n64_ledge' or key == 'n64_drop_through':
            self.pop('n64_point_flags', None)          # (a whole-line setting now)
        _refresh_look(self)
    return bpy.props.BoolProperty(get=get, set=set, name=name, description=description)


def obj_int(key, lo, hi, name='', description=''):
    def get(self):
        return int(self.get(key, 0))

    def set(self, value):
        self[key] = int(value)
        if key == 'n64_surface':
            self.pop('n64_point_flags', None)
        _refresh_look(self)
    return bpy.props.IntProperty(get=get, set=set, min=lo, max=hi, name=name, description=description)


def _collision_edit_selection(context=None):
    context = context or bpy.context
    ob = context.object
    if ob is None or ob.get('n64_gamedata') != 'collision_group' or context.mode != 'EDIT_MESH':
        return None
    import bmesh
    bm = bmesh.from_edit_mesh(ob.data)
    layers = {name: bm.edges.layers.int.get(name) for name in
              (COLL_EDGE_TYPE, COLL_EDGE_FLAGS, COLL_EDGE_START_SIDE, COLL_EDGE_END_FLAGS)}
    if any(layer is None for layer in layers.values()):
        return None
    selected_vertices = [vertex for vertex in bm.verts if vertex.select]
    vertex_mode = context.tool_settings.mesh_select_mode[0]
    if vertex_mode and selected_vertices:
        selected_edges = [edge for edge in bm.edges
                          if edge.verts[0] in selected_vertices or edge.verts[1] in selected_vertices]
    else:
        selected_edges = [edge for edge in bm.edges if edge.select]
    if not selected_edges and selected_vertices:
        selected_edges = [edge for edge in bm.edges
                          if edge.verts[0] in selected_vertices or edge.verts[1] in selected_vertices]
    if not selected_vertices:
        selected_vertices = list({vertex for edge in selected_edges for vertex in edge.verts})
    bm.verts.index_update()
    selected_vertices.sort(key=lambda vertex: vertex.index)
    active = bm.select_history.active
    active_edge = active if active in selected_edges else None
    active_vertex = active if active in selected_vertices else None
    if active_vertex is None and active_edge is not None:
        side = int(active_edge[layers[COLL_EDGE_START_SIDE]])
        active_vertex = active_edge.verts[side] if side in (0, 1) else active_edge.verts[0]
    if active_vertex is None and selected_vertices:
        active_vertex = selected_vertices[-1]
    if active_edge is None and active_vertex is not None:
        active_edge = next((edge for edge in selected_edges if active_vertex in edge.verts), None)
    if active_edge is None and selected_edges:
        active_edge = selected_edges[-1]
    return dict(ob=ob, bm=bm, layers=layers, edges=selected_edges, vertices=selected_vertices,
                active_vertex=active_vertex, active_edge=active_edge, vertex_mode=vertex_mode)


def _collision_vertex_flags(selection, vertex):
    bm, layers = selection['bm'], selection['layers']
    values, active_value = [], None
    for edge in bm.edges:
        if vertex not in edge.verts:
            continue
        start_side = int(edge[layers[COLL_EDGE_START_SIDE]])
        if start_side not in (0, 1):
            continue
        flags = int(edge[layers[COLL_EDGE_FLAGS if edge.verts[start_side] == vertex
                                 else COLL_EDGE_END_FLAGS]])
        values.append(flags)
        if edge == selection['active_edge']:
            active_value = flags
    if active_value is not None:
        return active_value
    return values[0] if values and len(set(values)) == 1 else None


def _active_collision_flags(selection):
    if selection is None:
        return None
    vertex = selection['active_vertex']
    if vertex is not None:
        flags = _collision_vertex_flags(selection, vertex)
        if flags is not None:
            return flags
    edge = selection['active_edge']
    if edge is None:
        return None
    layers = selection['layers']
    return int(edge[layers[COLL_EDGE_FLAGS]])


def _set_collision_point_flags(selection, flag=None, value=None, surface=None):
    if selection is None or not selection['vertices']:
        return
    bm, layers = selection['bm'], selection['layers']
    vertices = set(selection['vertices'])
    for edge in bm.edges:
        for side, vertex in enumerate(edge.verts):
            if vertex not in vertices:
                continue
            start_side = int(edge[layers[COLL_EDGE_START_SIDE]])
            if start_side not in (0, 1):
                continue
            layer = layers[COLL_EDGE_FLAGS if side == start_side else COLL_EDGE_END_FLAGS]
            flags = int(edge[layer])
            if flag is not None:
                flags = flags | flag if value else flags & ~flag
            if surface is not None:
                flags = (flags & ~0xFF) | (surface & 0xFF)
            edge[layer] = flags
    import bmesh
    bmesh.update_edit_mesh(selection['ob'].data, loop_triangles=False, destructive=False)


def _collision_type_get(_scene):
    selection = _collision_edit_selection()
    if selection is None or not selection['edges']:
        return 0
    if selection['vertex_mode'] and selection['active_vertex'] is not None:
        edges = [edge for edge in selection['bm'].edges
                 if selection['active_vertex'] in edge.verts]
    else:
        edges = selection['edges']
    types = {int(edge[selection['layers'][COLL_EDGE_TYPE]]) for edge in edges}
    if len(types) != 1:
        return len(LINE_KINDS)
    kind = next(iter(types))
    return kind if 0 <= kind < len(LINE_KINDS) else 0


def _collision_type_set(_scene, value):
    if value >= len(LINE_KINDS):
        return
    selection = _collision_edit_selection()
    if selection is None:
        return
    for edge in selection['edges']:
        edge[selection['layers'][COLL_EDGE_TYPE]] = value
    import bmesh
    bmesh.update_edit_mesh(selection['ob'].data, loop_triangles=False, destructive=False)


def _collision_bool_prop(flag, name):
    def get(_scene):
        flags = _active_collision_flags(_collision_edit_selection())
        return bool(flags is not None and flags & flag)

    def set(_scene, value):
        _set_collision_point_flags(_collision_edit_selection(), flag=flag, value=bool(value))

    return bpy.props.BoolProperty(get=get, set=set, name=name)


def _collision_surface_get(_scene):
    flags = _active_collision_flags(_collision_edit_selection())
    return flags & 0xFF if flags is not None else 0


def _collision_surface_set(_scene, value):
    _set_collision_point_flags(_collision_edit_selection(), surface=int(value))


COLLISION_TYPE_ITEMS = LINE_KINDS + [('mixed', 'Mixed', 'Selected collision edges have different types')]
COLLISION_SCENE_PROPS = {
    'n64ui_coll_type': bpy.props.EnumProperty(items=COLLISION_TYPE_ITEMS, get=_collision_type_get,
                                              set=_collision_type_set, name='Type',
                                              description='Collision type; changes apply to selected edges'),
    'n64_ui_drop': _collision_bool_prop(FLAG_DROP, 'Drop-Through'),
    'n64_ui_ledge': _collision_bool_prop(FLAG_LEDGE, 'Ledge'),
    'n64_ui_surface': bpy.props.IntProperty(get=_collision_surface_get, set=_collision_surface_set,
                                            min=0, max=255, name='Surface'),
}


GAME_TO_BLENDER = mathutils.Matrix(((1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))


def _hb_bone(ob):
    if ob.parent is not None and ob.parent.type == 'ARMATURE' and ob.parent_type == 'BONE' and \
            ob.parent_bone in ob.parent.data.bones:
        return ob.parent, ob.parent.data.bones[ob.parent_bone]
    return None, None


def _joint_world(arm, bone_name):
    """The joint's rest matrix in game space (stored by build_blend.py), or None."""
    w = (arm.get('n64_joint_worlds') or {}).get(bone_name) if arm is not None else None
    return mathutils.Matrix([list(w[r * 4:r * 4 + 4]) for r in range(4)]) if w else None


def _bone_parent_rest(arm, bone):
    """What a bone-parented object's matrix is relative to, at the rest pose."""
    return arm.matrix_world @ bone.matrix_local @ mathutils.Matrix.Translation((0, bone.length, 0))


def _hb_local(ob):
    """(offset, size) in the joint's space, game units; zeros when it can't be known."""
    arm, bone = _hb_bone(ob)
    W = _joint_world(arm, bone.name) if bone else None
    if W is None:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    world = _bone_parent_rest(arm, bone) @ ob.matrix_parent_inverse @ ob.matrix_basis
    L = W.inverted() @ GAME_TO_BLENDER.inverted() @ world
    half = 2.0 if ob.get('n64_hurtbox_half', True) else 1.0
    return tuple(L.translation), tuple(half * L.col[k].xyz.length for k in range(3))


def _hb_set(ob, offset=None, size=None):
    arm, bone = _hb_bone(ob)
    W = _joint_world(arm, bone.name) if bone else None
    if W is None:
        return
    off, sz = _hb_local(ob)
    off, sz = offset if offset is not None else off, size if size is not None else sz
    L = mathutils.Matrix.Translation(off) @ mathutils.Matrix.Diagonal((*(max(v, 0.001) / 2 for v in sz), 1.0))
    ob['n64_hurtbox_half'] = True
    ob.matrix_basis = (_bone_parent_rest(arm, bone) @ ob.matrix_parent_inverse).inverted() @ \
        GAME_TO_BLENDER @ W @ L


def line_node_group():
    """'N64 Collision Line': draws a line mesh (an open chain of vertices) as a thick square
    tube with a dot on every vertex. Display only: the import reads the mesh's vertices."""
    ng = bpy.data.node_groups.get('N64 Collision Line')
    if ng is None:
        ng = bpy.data.node_groups.new('N64 Collision Line', 'GeometryNodeTree')
        ng.interface.new_socket('Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
        ng.interface.new_socket('Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
        n_in, n_out = ng.nodes.new('NodeGroupInput'), ng.nodes.new('NodeGroupOutput')
        curve = ng.nodes.new('GeometryNodeMeshToCurve')
        prof = ng.nodes.new('GeometryNodeCurvePrimitiveQuadrilateral')
        tube = ng.nodes.new('GeometryNodeCurveToMesh')
        dots = ng.nodes.new('GeometryNodeMeshToPoints')
        join = ng.nodes.new('GeometryNodeJoinGeometry')
        for k, n in enumerate((n_in, curve, prof, tube, dots, join, n_out)):
            n.location = (180 * k, -80 * (k % 2))
        link = ng.links.new
        link(n_in.outputs[0], curve.inputs['Mesh'])
        link(curve.outputs['Curve'], tube.inputs['Curve'])
        link(prof.outputs['Curve'], tube.inputs['Profile Curve'])
        link(n_in.outputs[0], dots.inputs['Mesh'])
        link(tube.outputs['Mesh'], join.inputs[0])
        link(dots.outputs['Points'], join.inputs[0])
    prof = next((n for n in ng.nodes if n.bl_idname == 'GeometryNodeCurvePrimitiveQuadrilateral'), None)
    dots = next((n for n in ng.nodes if n.bl_idname == 'GeometryNodeMeshToPoints'), None)
    if prof is None or dots is None:
        raise RuntimeError('N64 Collision Line node group is missing its profile or vertex-marker node')
    prof.inputs['Width'].default_value = prof.inputs['Height'].default_value = 2 * LINE_RADIUS
    dots.inputs['Radius'].default_value = LINE_VERTEX_RADIUS
    return ng


def _collision_display_material(kind, drop=False):
    r, g, b, a = LINE_COLORS[kind]
    if drop:
        r, g, b = (r + 1) / 2, (g + 1) / 2, (b + 1) / 2
    suffix = ' drop-through' if drop else ''
    return _helper_mat(f'N64 Collision {kind}{suffix}', (r, g, b, a))


def collision_group_node_group():
    """Persistent viewport display for editable collision-group edge meshes."""
    ng = bpy.data.node_groups.get('N64 Collision Group Renderer')
    if ng is not None:
        return ng
    ng = bpy.data.node_groups.new('N64 Collision Group Renderer', 'GeometryNodeTree')
    ng.interface.new_socket('Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket('Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    nodes, links = ng.nodes, ng.links
    group_in, group_out = nodes.new('NodeGroupInput'), nodes.new('NodeGroupOutput')
    join = nodes.new('GeometryNodeJoinGeometry')
    link = links.new
    link(group_in.outputs['Geometry'], join.inputs['Geometry'])
    link(join.outputs['Geometry'], group_out.inputs['Geometry'])
    line_profile = nodes.new('GeometryNodeCurvePrimitiveCircle')
    line_profile.inputs['Resolution'].default_value = 8
    line_profile.inputs['Radius'].default_value = LINE_RADIUS / 2

    type_attr = nodes.new('GeometryNodeInputNamedAttribute')
    type_attr.data_type = 'INT'
    type_attr.inputs['Name'].default_value = COLL_EDGE_TYPE
    flags_attr = nodes.new('GeometryNodeInputNamedAttribute')
    flags_attr.data_type = 'INT'
    flags_attr.inputs['Name'].default_value = COLL_EDGE_FLAGS
    drop_modulo = nodes.new('ShaderNodeMath')
    drop_modulo.operation = 'MODULO'
    drop_modulo.inputs[1].default_value = 32768
    link(flags_attr.outputs['Attribute'], drop_modulo.inputs[0])
    drop_test = nodes.new('FunctionNodeCompare')
    drop_test.data_type = 'FLOAT'
    drop_test.operation = 'GREATER_EQUAL'
    drop_test.inputs['B'].default_value = 16384
    link(drop_modulo.outputs[0], drop_test.inputs['A'])

    def compare(field, data_type, operation, value, x, y):
        test = nodes.new('FunctionNodeCompare')
        test.data_type = data_type
        test.operation = operation
        test.inputs['B'].default_value = value
        link(field, test.inputs['A'])
        test.location = (x, y)
        return test.outputs['Result']

    for kind_index, (kind, _label, _description) in enumerate(LINE_KINDS):
        type_test = compare(type_attr.outputs['Attribute'], 'INT', 'EQUAL', kind_index, -760, -kind_index * 520)
        type_sep = nodes.new('GeometryNodeSeparateGeometry')
        type_sep.domain = 'EDGE'
        type_sep.location = (-520, -kind_index * 520)
        link(group_in.outputs['Geometry'], type_sep.inputs['Geometry'])
        link(type_test, type_sep.inputs['Selection'])
        flag_sep = nodes.new('GeometryNodeSeparateGeometry')
        flag_sep.domain = 'EDGE'
        flag_sep.location = (-300, -kind_index * 520)
        link(type_sep.outputs['Selection'], flag_sep.inputs['Geometry'])
        link(drop_test.outputs['Result'], flag_sep.inputs['Selection'])

        for is_drop, selection in ((False, flag_sep.outputs['Inverted']),
                                   (True, flag_sep.outputs['Selection'])):
            to_curve = nodes.new('GeometryNodeMeshToCurve')
            to_curve.location = (-80, -kind_index * 520 - int(is_drop) * 220)
            link(selection, to_curve.inputs['Mesh'])
            to_mesh = nodes.new('GeometryNodeCurveToMesh')
            to_mesh.location = (120, -kind_index * 520 - int(is_drop) * 220)
            link(to_curve.outputs['Curve'], to_mesh.inputs['Curve'])
            link(line_profile.outputs['Curve'], to_mesh.inputs['Profile Curve'])
            material = nodes.new('GeometryNodeSetMaterial')
            material.location = (320, -kind_index * 520 - int(is_drop) * 220)
            material.inputs['Material'].default_value = _collision_display_material(kind, is_drop)
            link(to_mesh.outputs['Mesh'], material.inputs['Geometry'])
            link(material.outputs['Geometry'], join.inputs['Geometry'])

    points = nodes.new('GeometryNodeMeshToPoints')
    points.location = (-300, 240)
    link(group_in.outputs['Geometry'], points.inputs['Mesh'])
    sphere = nodes.new('GeometryNodeMeshIcoSphere')
    sphere.location = (-300, 40)
    sphere.inputs['Radius'].default_value = LINE_VERTEX_RADIUS / 3
    sphere.inputs['Subdivisions'].default_value = 1
    sphere_material = nodes.new('GeometryNodeSetMaterial')
    sphere_material.location = (-80, 40)
    sphere_material.inputs['Material'].default_value = _helper_mat('N64 Collision Vertices', (1, 1, 1, 1))
    link(sphere.outputs['Mesh'], sphere_material.inputs['Geometry'])
    instances = nodes.new('GeometryNodeInstanceOnPoints')
    instances.location = (120, 240)
    link(points.outputs['Points'], instances.inputs['Points'])
    link(sphere_material.outputs['Geometry'], instances.inputs['Instance'])
    realize_spheres = nodes.new('GeometryNodeRealizeInstances')
    realize_spheres.location = (320, 240)
    link(instances.outputs['Instances'], realize_spheres.inputs['Geometry'])
    link(realize_spheres.outputs['Geometry'], join.inputs['Geometry'])

    floor_test = compare(type_attr.outputs['Attribute'], 'INT', 'EQUAL', 0, -760, 280)
    ledge_test = compare(flags_attr.outputs['Attribute'], 'INT', 'GREATER_EQUAL', 32768, -760, 80)
    ledge_and = nodes.new('FunctionNodeBooleanMath')
    ledge_and.operation = 'AND'
    ledge_and.location = (-520, 200)
    link(floor_test, ledge_and.inputs[0])
    link(ledge_test, ledge_and.inputs[1])
    ledge_sep = nodes.new('GeometryNodeSeparateGeometry')
    ledge_sep.domain = 'EDGE'
    ledge_sep.location = (-300, -240)
    link(group_in.outputs['Geometry'], ledge_sep.inputs['Geometry'])
    link(ledge_and.outputs['Boolean'], ledge_sep.inputs['Selection'])
    ledge_curve = nodes.new('GeometryNodeMeshToCurve')
    ledge_curve.location = (-80, -240)
    link(ledge_sep.outputs['Selection'], ledge_curve.inputs['Mesh'])
    ledge_points = nodes.new('GeometryNodeCurveToPoints')
    ledge_points.mode = 'COUNT'
    ledge_points.inputs['Count'].default_value = 2
    ledge_points.location = (120, -240)
    link(ledge_curve.outputs['Curve'], ledge_points.inputs['Curve'])
    ring_circle = nodes.new('GeometryNodeMeshCircle')
    ring_circle.inputs['Vertices'].default_value = 16
    ring_circle.inputs['Radius'].default_value = LINE_VERTEX_RADIUS
    ring_path = nodes.new('GeometryNodeMeshToCurve')
    ring_path.location = (-80, -520)
    link(ring_circle.outputs['Mesh'], ring_path.inputs['Mesh'])
    ring_profile = nodes.new('GeometryNodeCurvePrimitiveCircle')
    ring_profile.inputs['Resolution'].default_value = 6
    ring_profile.inputs['Radius'].default_value = 5
    ring_mesh = nodes.new('GeometryNodeCurveToMesh')
    ring_mesh.location = (120, -520)
    link(ring_path.outputs['Curve'], ring_mesh.inputs['Curve'])
    link(ring_profile.outputs['Curve'], ring_mesh.inputs['Profile Curve'])
    ring_transform = nodes.new('GeometryNodeTransform')
    ring_transform.location = (320, -520)
    ring_transform.inputs['Rotation'].default_value = (math.pi / 2, 0, 0)
    link(ring_mesh.outputs['Mesh'], ring_transform.inputs['Geometry'])
    ring_material = nodes.new('GeometryNodeSetMaterial')
    ring_material.location = (520, -520)
    ring_material.inputs['Material'].default_value = _helper_mat('N64 Collision Ledge', (1, 1, 0.2, 1))
    link(ring_transform.outputs['Geometry'], ring_material.inputs['Geometry'])
    ring_instances = nodes.new('GeometryNodeInstanceOnPoints')
    ring_instances.location = (520, -240)
    link(ledge_points.outputs['Points'], ring_instances.inputs['Points'])
    link(ring_material.outputs['Geometry'], ring_instances.inputs['Instance'])
    realize_rings = nodes.new('GeometryNodeRealizeInstances')
    realize_rings.location = (720, -240)
    link(ring_instances.outputs['Instances'], realize_rings.inputs['Geometry'])
    link(realize_rings.outputs['Geometry'], join.inputs['Geometry'])
    return ng


def _set_collision_group_renderer(ob):
    ob.show_in_front = True
    modifier = next((mod for mod in ob.modifiers if mod.type == 'NODES'
                     and mod.name == 'N64 Collision Display'), None)
    if modifier is None:
        modifier = ob.modifiers.new('N64 Collision Display', 'NODES')
    modifier.node_group = collision_group_node_group()
    modifier.show_render = False


def new_line(name, pts):
    """A collision line object: vertices (x, 0, y) chained by edges, with the tube modifier."""
    me = bpy.data.meshes.new(name)
    me.from_pydata([(x, 0.0, y) for x, y in pts], [(i, i + 1) for i in range(len(pts) - 1)], [])
    ob = bpy.data.objects.new(name, me)
    ob.show_in_front = True
    ob.modifiers.new('N64 Line Look', 'NODES').node_group = line_node_group()
    return ob


def _line_group(ob):
    if ob is None:
        return None
    return next((int(c.name[6:]) for c in ob.users_collection if re.match(r'Group \d+$', c.name)), None)


def _sel_lines(context):
    sel = [o for o in context.selected_objects if o.get('n64_gamedata') in ('collision', 'collision_group')]
    act = context.object
    if act is not None and act.get('n64_gamedata') in ('collision', 'collision_group') and act not in sel:
        sel.insert(0, act)
    return sel


def _collision_paths(ob):
    line_flags = (FLAG_DROP if ob.get('n64_drop_through') else 0) | \
        (FLAG_LEDGE if ob.get('n64_ledge') else 0) | int(ob.get('n64_surface', 0))
    point_flags = ob.get('n64_point_flags') or []

    def path(locals_, cyclic=False, flag_indices=None):
        indices = flag_indices if flag_indices is not None else range(len(locals_))
        coords = [ob.matrix_world @ local for local in locals_]
        flags = [int(point_flags[i]) if i < len(point_flags) else line_flags for i in indices]
        return coords, flags, cyclic

    paths = []
    if ob.type == 'MESH':
        adjacency = [set() for _ in ob.data.vertices]
        for edge in ob.data.edges:
            a, b = edge.vertices
            adjacency[a].add(b)
            adjacency[b].add(a)
        ends = [i for i, neighbors in enumerate(adjacency) if len(neighbors) == 1]
        ordered, seen = [], set()
        if len(ends) == 2 and all(len(n) in (1, 2) for n in adjacency):
            previous, current = None, min(ends)
            while current not in seen:
                ordered.append(current)
                seen.add(current)
                following = [i for i in adjacency[current] if i != previous]
                if not following:
                    break
                previous, current = current, following[0]
        if ordered and len(seen) == len(ob.data.vertices):
            paths.append(path([ob.data.vertices[i].co for i in ordered], flag_indices=ordered))
        else:
            paths.extend(path([ob.data.vertices[i].co for i in (a, b)], flag_indices=(a, b))
                         for a, b in (e.vertices for e in ob.data.edges))
    elif ob.type == 'CURVE':
        offset = 0
        for spline in ob.data.splines:
            points = spline.points if spline.type in ('POLY', 'NURBS') else spline.bezier_points
            paths.append(path([p.co.xyz if hasattr(p.co, 'xyz') else p.co for p in points],
                              spline.use_cyclic_u, range(offset, offset + len(points))))
            offset += len(points)
        return paths
    return paths


def _collision_group_paths(ob):
    me = ob.data
    if ob.mode == 'EDIT':
        import bmesh
        bm = bmesh.from_edit_mesh(me)
        layers = {name: bm.edges.layers.int.get(name) for name in
                  (COLL_EDGE_TYPE, COLL_EDGE_FLAGS, COLL_EDGE_START_SIDE)}
        if any(layer is None for layer in layers.values()):
            raise RuntimeError(f'{ob.name}: collision edge type/flags are missing')
        edge_data = ((edge.verts, edge[layers[COLL_EDGE_TYPE]], edge[layers[COLL_EDGE_FLAGS]],
                      edge[layers[COLL_EDGE_START_SIDE]]) for edge in bm.edges)
        vertices = bm.verts
    else:
        attributes = {name: me.attributes.get(name) for name in
                      (COLL_EDGE_TYPE, COLL_EDGE_FLAGS, COLL_EDGE_START_SIDE)}
        if any(attr is None or attr.domain != 'EDGE' or attr.data_type != 'INT'
               for attr in attributes.values()):
            raise RuntimeError(f'{ob.name}: collision edge type/flags are missing')
        edge_data = ((edge.vertices, attributes[COLL_EDGE_TYPE].data[i].value,
                      attributes[COLL_EDGE_FLAGS].data[i].value,
                      attributes[COLL_EDGE_START_SIDE].data[i].value)
                     for i, edge in enumerate(me.edges))
        vertices = me.vertices
    group = _line_group(ob) or int(ob.get('n64_collision_group', 0))
    paths = []
    for endpoints, kind_index, flags, start_side in edge_data:
        a, b = endpoints
        if start_side:
            a, b = b, a
        kind_index = int(kind_index)
        if not 0 <= kind_index < len(LINE_KINDS):
            continue
        flags = int(flags)
        a = a.co if hasattr(a, 'co') else vertices[a].co
        b = b.co if hasattr(b, 'co') else vertices[b].co
        coords = [ob.matrix_world @ a, ob.matrix_world @ b]
        paths.append((LINE_KINDS[kind_index][0], group, coords, [flags, flags], False))
    return paths


def _floor_continues_both_sides(floor_segments, point):
    left = right = False
    for a, b in floor_segments:
        if a == point:
            other = b
        elif b == point:
            other = a
        else:
            continue
        left |= other.x < point.x
        right |= other.x > point.x
    return left and right


def _draw_collision_overlay():
    import gpu
    from bpy_extras import view3d_utils
    from gpu_extras.batch import batch_for_shader

    context = bpy.context
    region, rv3d = context.region, context.region_data
    if region is None or rv3d is None:
        return
    lines = []
    floor_segments = {}
    for ob in context.scene.objects:
        if ob.get('n64_gamedata') not in ('collision', 'collision_group') or not ob.visible_get():
            continue
        if ob.get('n64_gamedata') == 'collision_group':
            lines.extend(_collision_group_paths(ob))
            continue
        kind = ob.get('n64_coll_type', 'floor')
        group = _line_group(ob) or 0
        for coords, flags, cyclic in _collision_paths(ob):
            if len(coords) < 2:
                continue
            lines.append((kind, group, coords, flags, cyclic))

    for kind, group, coords, _flags, cyclic in lines:
        if kind == 'floor':
            segments = zip(coords, coords[1:] + ([coords[0]] if cyclic else []))
            floor_segments.setdefault(group, []).extend(segments)

    if not lines:
        return

    kinds = [item[0] for item in LINE_KINDS]
    solid, dashed, vertices, ledges = ({kind: [] for kind in kinds} for _ in range(4))
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')

    def screen_pos(world):
        return view3d_utils.location_3d_to_region_2d(region, rv3d, world)

    def add_circle(target, world, radius):
        center = screen_pos(world)
        if center is None:
            return
        points = []
        for i in range(12):
            for step in (i, i + 1):
                angle = 2 * math.pi * step / 12
                screen = center + mathutils.Vector((math.cos(angle) * radius, math.sin(angle) * radius))
                points.append(view3d_utils.region_2d_to_location_3d(region, rv3d, screen, world))
        target.extend(points)

    for kind, group, coords, flags, cyclic in lines:
        segments = list(zip(coords, coords[1:] + ([coords[0]] if cyclic else [])))
        segment_flags = flags[:len(segments)]
        for (a, b), fl in zip(segments, segment_flags):
            if fl & FLAG_DROP:
                sa, sb = screen_pos(a), screen_pos(b)
                if sa is None or sb is None:
                    continue
                length = (sb - sa).length
                if length <= 0:
                    continue
                for start in range(0, int(length), 15):
                    end = min(start + 10, length)
                    dashed[kind].extend((a.lerp(b, start / length), a.lerp(b, end / length)))
            else:
                solid[kind].extend((a, b))
        for i, point in enumerate(coords):
            add_circle(vertices[kind], point, VERTEX_PIXEL_RADIUS)
            previous = flags[(i - 1) % len(flags)] if i or cyclic else 0
            following = flags[i] if i < len(segments) else 0
            ledge = bool((previous | following) & FLAG_LEDGE)
            if kind == 'floor' and ledge and not _floor_continues_both_sides(
                    floor_segments.get(group, ()), point):
                add_circle(ledges[kind], point, LEDGE_PIXEL_RADIUS)

    try:
        gpu.state.depth_test_set('NONE')
        for kind in kinds:
            color = LINE_COLORS[kind]
            for width, positions in ((LINE_PIXEL_WIDTH, solid[kind]),
                                     (LINE_PIXEL_WIDTH, dashed[kind]),
                                     (2.0, vertices[kind]),
                                     (2.5, ledges[kind])):
                if not positions:
                    continue
                gpu.state.line_width_set(width)
                batch = batch_for_shader(shader, 'LINES', {'pos': positions})
                shader.bind()
                shader.uniform_float('color', color)
                batch.draw(shader)
    finally:
        gpu.state.line_width_set(1.0)
        gpu.state.depth_test_set('LESS_EQUAL')


def _register_collision_draw_handler():
    if bpy.app.background:
        return
    namespace = bpy.app.driver_namespace
    previous = namespace.pop(COLLISION_DRAW_HANDLER, None)
    if previous is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(previous, 'WINDOW')
        except ValueError:
            pass
    namespace[COLLISION_DRAW_HANDLER] = bpy.types.SpaceView3D.draw_handler_add(
        _draw_collision_overlay, (), 'WINDOW', 'POST_VIEW')


def _unregister_collision_draw_handler():
    handler = bpy.app.driver_namespace.pop(COLLISION_DRAW_HANDLER, None)
    if handler is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(handler, 'WINDOW')
        except ValueError:
            pass


class N64_OT_coll_apply(bpy.types.Operator):
    bl_idname = 'n64.coll_apply'
    bl_label = 'Copy Collision Properties'
    bl_description = 'Copy the active legacy collision line properties to selected line objects'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        act = context.object
        if act is None or act.get('n64_gamedata') != 'collision':
            return {'CANCELLED'}
        flags = (bool(act.get('n64_drop_through')), bool(act.get('n64_ledge')), int(act.get('n64_surface', 0)))
        for ob in _sel_lines(context):
            if ob is act:
                continue
            if (bool(ob.get('n64_drop_through')), bool(ob.get('n64_ledge')), int(ob.get('n64_surface', 0))) != flags:
                ob.pop('n64_point_flags', None)
            ob['n64_coll_type'] = act.get('n64_coll_type', 'floor')
            ob['n64_drop_through'], ob['n64_ledge'], ob['n64_surface'] = flags
            _refresh_look(ob)
        return {'FINISHED'}


def _collision_group_object(group):
    root = bpy.data.collections.get('Collision')
    if root is None:
        raise RuntimeError('No Collision collection (not a stage export)')
    collection = bpy.data.collections.get(f'Group {group}')
    if collection is None:
        collection = bpy.data.collections.new(f'Group {group}')
        collection.hide_render = True
    if collection.name not in root.children:
        root.children.link(collection)
    ob = next((o for o in collection.objects if o.get('n64_gamedata') == 'collision_group'), None)
    if ob is None:
        mesh = bpy.data.meshes.new(f'Collision Group {group}')
        for name in (COLL_EDGE_TYPE, COLL_EDGE_FLAGS, COLL_EDGE_ORDER, COLL_EDGE_SEGMENT,
                     COLL_EDGE_START_SIDE, COLL_EDGE_END_FLAGS, COLL_EDGE_POINT_FLAGS):
            mesh.attributes.new(name, 'INT', 'EDGE')
        ob = bpy.data.objects.new(f'Collision Group {group}', mesh)
        ob['n64_gamedata'] = 'collision_group'
        ob['n64_collision_group'] = group
        ob.show_in_front = True
        collection.objects.link(ob)
    _set_collision_group_renderer(ob)
    return ob


class N64_OT_coll_group(bpy.types.Operator):
    bl_idname = 'n64.coll_group'
    bl_label = 'Set Collision Group'
    bl_description = 'Move selected collision to this group; group meshes merge (the id hazards toggle)'
    bl_options = {'REGISTER', 'UNDO'}
    group: bpy.props.IntProperty(min=1, max=6)

    def execute(self, context):
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        root = bpy.data.collections.get('Collision')
        if root is None:
            return {'CANCELLED'}
        gc = bpy.data.collections.get(f'Group {self.group}')
        if gc is None:
            gc = bpy.data.collections.new(f'Group {self.group}')
            gc.hide_render = True
        if gc.name not in root.children:
            root.children.link(gc)
        selected = _sel_lines(context)
        group_meshes = [ob for ob in selected if ob.get('n64_gamedata') == 'collision_group']
        other_selected = [ob for ob in selected if ob.get('n64_gamedata') != 'collision_group']
        targets = [ob for ob in gc.objects if ob.get('n64_gamedata') == 'collision_group'
                   and ob not in group_meshes]
        if not targets and group_meshes:
            target, *group_meshes = group_meshes
            targets = [target]
            for collection in target.users_collection:
                collection.objects.unlink(target)
            gc.objects.link(target)
        if targets:
            target = targets[0]
            if group_meshes:
                for ob in context.selected_objects:
                    ob.select_set(False)
                target.select_set(True)
                for ob in group_meshes:
                    ob.select_set(True)
                context.view_layer.objects.active = target
                bpy.ops.object.join()
            target['n64_collision_group'] = self.group
        for ob in other_selected:
            for c in ob.users_collection:
                c.objects.unlink(ob)
            gc.objects.link(ob)
        return {'FINISHED'}


class N64_OT_coll_add(bpy.types.Operator):
    bl_idname = 'n64.coll_add'
    bl_label = 'Add Collision Edge'
    bl_description = 'Add a floor edge at the 3D cursor to this group mesh'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        if bpy.data.collections.get('Collision') is None:
            self.report({'ERROR'}, 'No Collision collection (not a stage export)')
            return {'CANCELLED'}
        act = context.object if context.object and context.object.get('n64_gamedata') == 'collision_group' else None
        group = _line_group(act) or 1
        ob = _collision_group_object(group)
        if context.mode != 'EDIT_MESH' or context.object is not ob:
            if context.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            for other in context.selected_objects:
                other.select_set(False)
            ob.hide_set(False)
            ob.select_set(True)
            context.view_layer.objects.active = ob
            bpy.ops.object.mode_set(mode='EDIT')
        import bmesh
        bm = bmesh.from_edit_mesh(ob.data)
        cursor = context.scene.cursor.location
        v0 = bm.verts.new(ob.matrix_world.inverted() @ mathutils.Vector((cursor.x - 500, 0, cursor.z)))
        v1 = bm.verts.new(ob.matrix_world.inverted() @ mathutils.Vector((cursor.x + 500, 0, cursor.z)))
        edge = bm.edges.new((v0, v1))
        bm.verts.index_update()
        bm.edges.index_update()
        names = (COLL_EDGE_TYPE, COLL_EDGE_FLAGS, COLL_EDGE_ORDER, COLL_EDGE_SEGMENT,
                 COLL_EDGE_START_SIDE, COLL_EDGE_END_FLAGS, COLL_EDGE_POINT_FLAGS)
        layers = {name: bm.edges.layers.int.get(name) or bm.edges.layers.int.new(name) for name in names}
        flags = (FLAG_DROP if context.scene.n64_ui_drop else 0) | \
            (FLAG_LEDGE if context.scene.n64_ui_ledge else 0) | int(context.scene.n64_ui_surface)
        kind = _collision_type_get(context.scene)
        edge[layers[COLL_EDGE_TYPE]] = kind if kind < len(LINE_KINDS) else 0
        edge[layers[COLL_EDGE_FLAGS]] = flags
        edge[layers[COLL_EDGE_ORDER]] = -1
        edge[layers[COLL_EDGE_SEGMENT]] = -1
        edge[layers[COLL_EDGE_START_SIDE]] = 0 if edge.verts[0] == v0 else 1
        edge[layers[COLL_EDGE_END_FLAGS]] = flags
        edge[layers[COLL_EDGE_POINT_FLAGS]] = 0
        for vertex in bm.verts:
            vertex.select = vertex in (v0, v1)
        for other in bm.edges:
            other.select = other == edge
        bm.select_flush_mode()
        bmesh.update_edit_mesh(ob.data, loop_triangles=False, destructive=True)
        return {'FINISHED'}


class N64_PT_collision_tools(bpy.types.Panel):
    bl_label = 'N64 Collision'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'N64'

    @classmethod
    def poll(cls, context):
        return bpy.data.collections.get('Collision') is not None

    def draw(self, context):
        lay = self.layout
        if not bpy.app.background and COLLISION_DRAW_HANDLER not in bpy.app.driver_namespace:
            _register_collision_draw_handler()
        act = context.object if (context.object is not None
                                 and context.object.get('n64_gamedata') in ('collision', 'collision_group')) else None
        sel = _sel_lines(context)
        if act is None:
            lay.label(text='Select a collision group mesh', icon='INFO')
        elif act.get('n64_gamedata') == 'collision_group':
            group = _line_group(act) or int(act.get('n64_collision_group', 1))
            lay.label(text=f'{act.name}: edit the mesh as collision edges', icon='MESH_DATA')
            if context.mode == 'EDIT_MESH':
                selection = _collision_edit_selection(context)
                if selection is None:
                    lay.label(text='Collision edge attributes are missing', icon='ERROR')
                elif not selection['edges']:
                    lay.label(text='Select a collision edge or vertex to edit')
                else:
                    lay.label(text=f'{len(selection["edges"])} edge(s), '
                                   f'{len(selection["vertices"])} vertex/vertices selected')
                col = lay.column()
                col.enabled = bool(selection and selection['edges'])
                col.use_property_split = True
                col.label(text='Active vertex/edge values; edits update the selection:')
                col.prop(context.scene, 'n64ui_coll_type')
                col.prop(context.scene, 'n64_ui_drop')
                col.prop(context.scene, 'n64_ui_ledge')
                col.prop(context.scene, 'n64_ui_surface')
            else:
                lay.label(text='Tab into Edit Mode and select edges or vertices to edit properties')
            lay.label(text='Group (hazards toggle 2+; 1 is always on):')
            row = lay.row(align=True)
            for g in range(1, 7):
                row.operator('n64.coll_group', text=str(g), depress=(g == group)).group = g
        else:
            lay.label(text=act.name, icon='IPO_LINEAR')
            lay.label(text='Type and flags apply to this whole line')
            col = lay.column()
            col.use_property_split = True
            col.prop(act, 'n64ui_coll_type')
            col.prop(act, 'n64_ui_drop')
            col.prop(act, 'n64_ui_ledge')
            col.prop(act, 'n64_ui_surface')
            lay.label(text='Group (hazards toggle 2+; 1 is always on):')
            cur = _line_group(act)
            row = lay.row(align=True)
            for g in range(1, 7):
                row.operator('n64.coll_group', text=str(g), depress=(g == cur)).group = g
            if len(sel) > 1:
                lay.operator('n64.coll_apply', text=f'Copy to {len(sel) - 1} Selected', icon='DUPLICATE')
        lay.operator('n64.coll_add', text='Add Collision Edge', icon='ADD')
        lay.label(text='Floor green; ceiling red; right blue; left orange')
        lay.label(text='Dashed: drop-through; ring: ledge; circles: vertices')
        lay.label(text='Viewport overlay only; hidden in final renders', icon='INFO')


def _kind_get(self):
    return int(self.get('n64_mapobj_kind', 4))


def _kind_set(self, value):
    self['n64_mapobj_kind'] = int(value)
    self.name = MAPOBJ_KINDS.get(int(value), f'kind 0x{int(value):02X}')


OBJECT_PROPS = {
    'n64ui_hb_placement': obj_enum('n64_hurtbox_placement', HB_PLACEMENTS, 'mid', name='Placement',
                                   description='Where a hit on this box counts: low, mid or high (the '
                                               'damage reaction)'),
    'n64ui_hb_grabbable': obj_bool('n64_hurtbox_grabbable', name='Grabbable',
                                   description='Grabs connect with this box (vanilla: torso, head; usually not '
                                               'arms or lower legs)'),
    'n64ui_hb_offset': bpy.props.FloatVectorProperty(
        size=3, get=lambda self: _hb_local(self)[0], set=lambda self, v: _hb_set(self, offset=v), precision=2,
        name='Offset', description='The box\'s center in its joint\'s space, game units (config.yaml offset)'),
    'n64ui_hb_size': bpy.props.FloatVectorProperty(
        size=3, get=lambda self: _hb_local(self)[1], set=lambda self, v: _hb_set(self, size=v), precision=2, min=0,
        name='Size', description='The box\'s full width along the joint\'s x / y / z, game units (config.yaml '
                                  'size; the game halves it)'),
    'n64ui_coll_type': obj_enum('n64_coll_type', LINE_KINDS, 'floor', name='Type',
                                description='Which side of the line is solid'),
    'n64_ui_drop': obj_bool('n64_drop_through', name='Drop-Through',
                            description='A platform: jumped through from below, dropped through by pressing down'),
    'n64_ui_ledge': obj_bool('n64_ledge', name='Ledge',
                             description='Its ends can be grabbed (a floor\'s edge where it doesn\'t continue)'),
    'n64_ui_surface': obj_int('n64_surface', 0, 255, name='Surface',
                              description='Surface kind (low byte of the vertex flags): 0 normal; others '
                                          'damage / slide... per stage code'),
    'n64ui_mapobj_kind': bpy.props.IntProperty(get=_kind_get, set=_kind_set, min=0, max=0xFF, name='Kind',
                                               description='Map object kind (decomp mpdef.h nMPMapObjKind): '
                                                           + ', '.join(f'{k}: {v}' for k, v in
                                                                       MAPOBJ_KINDS.items())),
}


def draw_hurtbox(lay, ob):
    lay.prop(ob, 'n64ui_hb_placement')
    lay.prop(ob, 'n64ui_hb_grabbable')
    arm, bone = _hb_bone(ob)
    if bone is not None and _joint_world(arm, bone.name) is not None:
        lay.prop(ob, 'n64ui_hb_offset')
        lay.prop(ob, 'n64ui_hb_size')


def hurtbox_objects(bone_name=None):
    obs = [o for o in bpy.data.objects if o.get('n64_gamedata') == 'hurtbox' and o.users_collection]
    if bone_name is not None:
        obs = [o for o in obs if (_hb_bone(o)[1] is not None and o.parent_bone == bone_name)]
    return sorted(obs, key=lambda o: o.name)


def _hurtbox_lc(context):
    return context.view_layer.layer_collection.children.get('Hurtboxes')


def _show_get(self):
    lc = _hurtbox_lc(bpy.context)
    return lc is not None and not lc.hide_viewport


def _show_set(self, value):
    lc = _hurtbox_lc(bpy.context)
    if lc is not None:
        lc.hide_viewport = not value


class N64_OT_hurtbox_add(bpy.types.Operator):
    bl_idname = 'n64.hurtbox_add'
    bl_label = 'Add Hurtbox'
    bl_description = 'A new hurtbox on this joint (up to 11 in all)'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        arm = context.object if context.object and context.object.type == 'ARMATURE' else None
        bone = context.active_bone
        if arm is None or bone is None or _joint_world(arm, bone.name) is None:
            self.report({'ERROR'}, 'Select a joint bone of the fighter\'s armature')
            return {'CANCELLED'}
        if len(hurtbox_objects()) >= 11:
            self.report({'ERROR'}, 'The game has room for 11 hurtboxes')
            return {'CANCELLED'}
        coll = bpy.data.collections.get('Hurtboxes') or bpy.data.collections.new('Hurtboxes')
        if coll.name not in context.scene.collection.children and not coll.users:
            context.scene.collection.children.link(coll)
        src = bpy.data.meshes.get('N64 Hurtbox')
        if src is None:
            src = bpy.data.meshes.new('N64 Hurtbox')
            v = [(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
            src.from_pydata(v, [], [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4),
                                    (1, 5, 7, 3)])
        ob = bpy.data.objects.new(f'hurtbox {bone.name} mid', src.copy())
        coll.objects.link(ob)
        ob['n64_gamedata'] = 'hurtbox'
        ob['n64_hurtbox_placement'] = 'mid'
        ob['n64_hurtbox_grabbable'] = False
        ob['n64_hurtbox_joint'] = int(bone.name[6:])
        ob['n64_hurtbox_half'] = True
        ob.show_wire = True
        ob.parent, ob.parent_type, ob.parent_bone = arm, 'BONE', bone.name
        ob.matrix_parent_inverse = mathutils.Matrix.Identity(4)
        _refresh_look(ob)
        _hb_set(ob, offset=(0.0, 0.0, 0.0), size=(100.0, 100.0, 100.0))
        lc = _hurtbox_lc(context)
        if lc is not None:
            lc.hide_viewport = False
        return {'FINISHED'}


class N64_OT_hurtbox_remove(bpy.types.Operator):
    bl_idname = 'n64.hurtbox_remove'
    bl_label = 'Remove Hurtbox'
    bl_description = 'Delete this hurtbox'
    bl_options = {'REGISTER', 'UNDO'}
    name: bpy.props.StringProperty()

    def execute(self, context):
        ob = bpy.data.objects.get(self.name)
        if ob is not None:
            bpy.data.objects.remove(ob)
        return {'FINISHED'}


class N64_OT_hurtbox_select(bpy.types.Operator):
    bl_idname = 'n64.hurtbox_select'
    bl_label = 'Select Hurtbox'
    bl_description = 'Select the box (Object Mode) to move / scale it in the viewport'
    bl_options = {'REGISTER', 'UNDO'}
    name: bpy.props.StringProperty()

    def execute(self, context):
        ob = bpy.data.objects.get(self.name)
        if ob is None:
            return {'CANCELLED'}
        lc = _hurtbox_lc(context)
        if lc is not None:
            lc.hide_viewport = False
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for o in context.selected_objects:
            o.select_set(False)
        ob.hide_set(False)
        ob.select_set(True)
        context.view_layer.objects.active = ob
        return {'FINISHED'}


def _context_bone(context):
    """The bone the Bone tab shows (Pose / Edit Mode), else the armature's active one."""
    return getattr(context, 'bone', None) or getattr(context, 'edit_bone', None) or \
        getattr(context, 'active_bone', None)


class N64_PT_bone_hurtboxes(bpy.types.Panel):
    bl_label = 'N64 Hurtboxes'
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'bone'

    @classmethod
    def poll(cls, context):
        bone = _context_bone(context)
        return bone is not None and bool(re.match(r'joint_\d+$', bone.name)) and \
            bpy.data.collections.get('Hurtboxes') is not None

    def draw(self, context):
        lay = self.layout
        bone = _context_bone(context)
        allboxes = hurtbox_objects()
        row = lay.row()
        row.prop(context.scene, 'n64ui_show_hurtboxes', text='Show Hurtboxes')
        row.label(text=f'{len(allboxes)} / 11 in all')
        for ob in hurtbox_objects(bone.name):
            box = lay.box()
            row = box.row()
            row.label(text=ob.name, icon='MESH_CUBE')
            row.operator('n64.hurtbox_select', text='', icon='RESTRICT_SELECT_OFF').name = ob.name
            row.operator('n64.hurtbox_remove', text='', icon='X').name = ob.name
            col = box.column()
            col.use_property_split = True
            draw_hurtbox(col, ob)
        lay.operator('n64.hurtbox_add', icon='ADD')


class N64_PT_gamedata(bpy.types.Panel):
    bl_label = 'N64 Game Data'
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'object'

    @classmethod
    def poll(cls, context):
        return context.object is not None and bool(context.object.get('n64_gamedata'))

    def draw(self, context):
        lay = self.layout
        lay.use_property_split = True
        ob = context.object
        kind = ob.get('n64_gamedata')
        if kind == 'hurtbox':
            joint = ob.parent_bone if ob.parent_type == 'BONE' else f'joint_{ob.get("n64_hurtbox_joint", 0):02d}'
            lay.label(text=f'Hurtbox on {joint} (Relations > Parent Bone)', icon='MESH_CUBE')
            lay.prop(ob, 'n64ui_hb_placement')
            lay.prop(ob, 'n64ui_hb_grabbable')
            draw_hurtbox(lay, ob)
            lay.label(text='Move / scale it along the joint\'s axes (no rotation)', icon='INFO')
            lay.label(text='Also in the bone\'s Bone tab (Pose / Edit Mode)')
            lay.label(text='The import orders them like vanilla: torso, head, arms, legs')
        elif kind == 'collision':
            group = next((c.name for c in ob.users_collection if c.name.startswith('Group ')), '(no group)')
            lay.label(text=f'Collision line, {group} (move it to another Group N collection)', icon='CURVE_PATH')
            lay.prop(ob, 'n64ui_coll_type')
            lay.prop(ob, 'n64_ui_drop')
            lay.prop(ob, 'n64_ui_ledge')
            lay.prop(ob, 'n64_ui_surface')
            lay.label(text='Also in the 3D view sidebar (N): N64 tab', icon='INFO')
            if 'n64_point_flags' in ob.keys():
                lay.label(text='Flags per point (from the game file): kept while these are unchanged',
                          icon='INFO')
            lay.label(text='Mesh vertices: Edit Mode, on y = 0 (front view, numpad 1)', icon='INFO')
        elif kind == 'collision_group':
            group = _line_group(ob) or int(ob.get('n64_collision_group', 1))
            lay.label(text=f'Collision group {group}: edit the mesh as edges', icon='MESH_DATA')
            lay.label(text='Select edges in Edit Mode; set type and flags in the 3D View N64 tab')
        elif kind == 'map_object':
            lay.label(text='Map object', icon='EMPTY_AXIS')
            lay.prop(ob, 'n64ui_mapobj_kind')
            lay.label(text=MAPOBJ_KINDS.get(int(ob.get('n64_mapobj_kind', 0)), 'unknown kind'))
        elif kind == 'spawn':
            lay.label(text=f'Spawn location: {ob.get("n64_spawn_category")} {ob.get("n64_spawn_player")}',
                      icon='EMPTY_SINGLE_ARROW')
            lay.label(text='config.yaml spawn_locations (the fighter\'s feet)')
        elif kind == 'bounds':
            lay.label(text=f'{ob.name}: scale / move its points (Edit Mode)', icon='SELECT_SET')


CLASSES = (N64_OT_joint_anim_set, N64_OT_joint_anim_remove, N64_PT_joint, N64_OT_anim_add, N64_OT_anim_remove, N64_OT_anim_settings, N64_OT_frame_add, N64_OT_frame_remove,
           N64_OT_texture_add, N64_OT_texture_remove, N64_OT_state_add, N64_OT_state_edit, N64_OT_state_remove, N64_PT_material, N64_PT_scene, N64_PT_gamedata,
           N64_OT_hurtbox_add, N64_OT_hurtbox_remove, N64_OT_hurtbox_select, N64_PT_bone_hurtboxes,
           N64_OT_coll_apply, N64_OT_coll_group, N64_OT_coll_add, N64_PT_collision_tools,
           N64_PT_motion_flags)


def _collapse_visible_outliners():
    """Collapse every entry of the outliners shown now (each screen once: the scene's
    n64_collapsed_screens). The outliner operator only works on a visible area, so other
    workspaces' outliners are collapsed when they're first shown."""
    scene = bpy.context.scene
    if scene is None or not scene.get('n64_collapse_outliner'):
        return
    done = set(scene.get('n64_collapsed_screens') or [])
    for win in bpy.context.window_manager.windows:
        scr = win.screen
        if scr is None or scr.name in done:
            continue
        found = False
        for area in scr.areas:
            if area.type != 'OUTLINER':
                continue
            region = next((r for r in area.regions if r.type == 'WINDOW'), None)
            if region is None:
                continue
            found = True
            with bpy.context.temp_override(window=win, screen=scr, area=area, region=region):
                for _ in range(12):            # one level per call, deepest first
                    try:
                        bpy.ops.outliner.show_one_level(open=False)
                    except RuntimeError:
                        break
            area.tag_redraw()
        if found:
            done.add(scr.name)
    scene['n64_collapsed_screens'] = sorted(done)


def _collapse_outliner():
    """On open of a generated .blend: the outliners collapsed (retried while the window
    builds its outliner tree)."""
    _collapse_outliner.tries = getattr(_collapse_outliner, 'tries', 0) + 1
    scene = bpy.context.scene
    if scene is None or not scene.get('n64_collapse_outliner'):
        return None
    scene['n64_collapsed_screens'] = []             # (again: the tree may not have been built yet)
    _collapse_visible_outliners()
    return 0.75 if _collapse_outliner.tries < 3 else None


def _workspace_changed(*_):
    """A workspace shown for the first time: its outliner collapsed."""
    bpy.app.timers.register(_collapse_visible_outliners_once, first_interval=0.1)


def _collapse_visible_outliners_once():
    _collapse_visible_outliners()
    return None


LOOP_DESC = 'The animation starts over after its length (its curves repeat: Cycles); off: it stops there'


def register():
    if bpy.data.node_groups.get('N64 Collision Line') is not None:
        line_node_group()
    for ob in bpy.data.objects:
        if ob.get('n64_gamedata') == 'collision':
            ob.show_in_front = True
            if ob.type == 'CURVE':
                ob.data.bevel_depth = LINE_RADIUS
        elif ob.get('n64_gamedata') == 'collision_group':
            ob.show_in_front = True
            _set_collision_group_renderer(ob)
    _register_collision_draw_handler()
    for name, prop in {**PROPS, **ADV_PROPS}.items():
        setattr(bpy.types.Material, name, prop)
    bpy.types.Material.n64ui_anim_loop = bpy.props.BoolProperty(get=_mat_loop_get, set=_mat_loop_set,
                                                                name='Loop', description=LOOP_DESC)
    bpy.types.PoseBone.n64ui_anim_loop = bpy.props.BoolProperty(get=_bone_loop_get, set=_bone_loop_set,
                                                                name='Loop', description=LOOP_DESC)
    bpy.types.Action.n64ui_flag_bits = bpy.props.BoolVectorProperty(
        size=32, get=_flag_bits_get, set=_flag_bits_set, name='Motion Flags',
        description='The animation\'s motion flags (anim_desc): bit 31 - i adds the fighter\'s hidden part i '
                    '(a joint) to its tree, the low bits are modes')
    bpy.types.Action.n64ui_anim_loop = bpy.props.BoolProperty(
        get=_anim_loop_get, set=_anim_loop_set, name='Loop',
        description='The animation starts over at its end (its scripts loop); off: it ends there')
    _remove_dopesheet_draws()
    if hasattr(bpy.types, 'DOPESHEET_PT_action'):
        bpy.types.DOPESHEET_PT_action.prepend(_dopesheet_n64_draw)
    old_tab = getattr(bpy.types, 'N64_PT_motion_flags_dopesheet', None)      # (an older version's
    if old_tab is not None:                                                  # N64 tab panel)
        try:
            bpy.utils.unregister_class(old_tab)
        except RuntimeError:
            pass
    _hide_pose_asset_panel()
    bpy.types.Action.n64ui_flags_hex = bpy.props.StringProperty(
        get=_flags_hex_get, set=_flags_hex_set, name='Flags',
        description='The motion flags as the game stores them (hex): the checkboxes below, combined')
    handlers = bpy.app.handlers.depsgraph_update_post
    for h in [h for h in handlers if getattr(h, '__name__', '') == '_sync_loops_on_update']:
        handlers.remove(h)                         # (registered again: the add-on + embedded copy)
    handlers.append(_sync_loops_on_update)
    for name, prop in SCENE_PROPS.items():
        setattr(bpy.types.Scene, name, prop)
    for name, prop in COLLISION_SCENE_PROPS.items():
        setattr(bpy.types.Scene, name, prop)
    for name, prop in OBJECT_PROPS.items():
        setattr(bpy.types.Object, name, prop)
    bpy.types.Scene.n64ui_show_hurtboxes = bpy.props.BoolProperty(
        get=_show_get, set=_show_set, name='Show Hurtboxes', description='Show the Hurtboxes collection')
    for c in CLASSES:
        try:
            bpy.utils.register_class(c)
        except ValueError:                 # already registered (add-on + embedded copy)
            pass
    if not bpy.app.background:
        _collapse_outliner.tries = 0
        bpy.app.timers.register(_collapse_outliner, first_interval=0.5)
        bpy.msgbus.subscribe_rna(key=(bpy.types.Window, 'workspace'), owner=_MSGBUS_OWNER, args=(),
                                 notify=_workspace_changed)
    # picking / opening an image on a texture node refreshes the N64 view
    bpy.msgbus.subscribe_rna(key=(bpy.types.ShaderNodeTexImage, 'image'), owner=_MSGBUS_OWNER, args=(),
                             notify=_image_changed)


def unregister():
    _unregister_collision_draw_handler()
    bpy.msgbus.clear_by_owner(_MSGBUS_OWNER)
    for c in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(c)
        except RuntimeError:
            pass
    for name in {**PROPS, **ADV_PROPS}:
        if hasattr(bpy.types.Material, name):
            delattr(bpy.types.Material, name)
    for name in SCENE_PROPS:
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for name in COLLISION_SCENE_PROPS:
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for name in OBJECT_PROPS:
        if hasattr(bpy.types.Object, name):
            delattr(bpy.types.Object, name)
    if hasattr(bpy.types.Scene, 'n64ui_show_hurtboxes'):
        delattr(bpy.types.Scene, 'n64ui_show_hurtboxes')
    for t in (bpy.types.Material, bpy.types.PoseBone):
        if hasattr(t, 'n64ui_anim_loop'):
            delattr(t, 'n64ui_anim_loop')
    for name in ('n64ui_flag_bits', 'n64ui_flags_hex', 'n64ui_anim_loop'):
        if hasattr(bpy.types.Action, name):
            delattr(bpy.types.Action, name)
    _remove_dopesheet_draws()
    _restore_pose_asset_panel()
    handlers = bpy.app.handlers.depsgraph_update_post
    for h in [h for h in handlers if getattr(h, '__name__', '') == '_sync_loops_on_update']:
        handlers.remove(h)


if __name__ == '__main__':
    register()
