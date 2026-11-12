"""N64 fighter material settings as a panel (Properties > Material > "N64 Fighter")
and scene light colors (Properties > Scene > "N64 Fighter"), with dropdowns, toggles
and color pickers instead of typed text.

They edit the same custom properties import_fighter.py reads (n64_format,
n64_wrap_s, ...), so a .blend works the same with or without this panel.

build_blend.py embeds this file in the .blend as a text block that registers
itself when Blender is allowed to run the file's scripts. It also installs as an
add-on: Edit > Preferences > Add-ons > Install from Disk.
"""
import bpy

bl_info = {
    'name': 'N64 Fighter Materials',
    'author': 'smashremix-extra',
    'version': (1, 0, 0),
    'blender': (3, 6, 0),
    'location': 'Properties > Material / Scene > N64 Fighter',
    'description': 'Texture format, wrap, culling, lighting and light colors for fighter_model',
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
    return next((n for n in m.node_tree.nodes if n.type == 'TEX_IMAGE' and n.image), None)


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
        for i, (axis, mode) in enumerate((('X', ws), ('Y', wt))):
            mth = _node(nt, f'N64 Wrap {axis}', 'ShaderNodeMath', x - 450, y + 120 - 240 * i)
            mth.use_clamp = False
            if mode == 'repeat':
                mth.operation = 'WRAP'
                mth.inputs[1].default_value, mth.inputs[2].default_value = 1.0, 0.0
            elif mode == 'mirror':
                mth.operation = 'PINGPONG'
                mth.inputs[1].default_value = 1.0
            else:                              # clamp: the edge pixels repeat
                mth.operation = 'ADD'
                mth.inputs[1].default_value = 0.0
                mth.use_clamp = True
            nt.links.new(sep.outputs[axis], mth.inputs[0])
            nt.links.new(mth.outputs[0], comb.inputs[axis])
        nt.links.new(comb.outputs[0], tex.inputs['Vector'])
    if out is None or bsdf is None:
        return
    build_combiner(m, nt, tex, out, bsdf)


def _srgb_lin(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def build_combiner(m, nt, tex, out, bsdf):
    """The material's N64 color combiner as shader nodes: two cycles of (a - b) * c + d,
    color and alpha, with its prim / env colors and the texture. SHADE is Blender's own
    lighting when lit (multiplied by the BSDF), the vertex color when unlit."""
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
    env_c, env_a = hex_color(0xFB, 0xFFFFFFFF)
    one_c, zero_c = rgb((1, 1, 1)), rgb((0, 0, 0))
    one, zero = val(1.0), val(0.0)
    tex_c = tex.outputs['Color'] if tex is not None else one_c
    tex_a = tex.outputs['Alpha'] if tex is not None else one
    if lit:
        shade_c, shade_a = one_c, one
    else:
        vc = node('ShaderNodeVertexColor')
        shade_c, shade_a = vc.outputs['Color'], vc.outputs['Alpha']
    P, Pa, E, Ea = rgb(prim_c), val(prim_a), rgb(env_c), val(env_a)
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
                'LOD_FRACTION': one, 'PRIM_LOD_FRAC': zero}
        asrc = {'COMBINED': comb_a, 'TEXEL0': tex_a, 'TEXEL1': tex_a, 'PRIM': Pa, 'SHADE': shade_a, 'ENV': Ea,
                '1': one, 'LOD_FRACTION': one, 'PRIM_LOD_FRAC': zero}
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
    blending = any(a == 0xE200001C and b not in (0xC4112078, 0xC4113078, 0xC4113878) for a, b in cmds)
    for attr, value in (('surface_render_method', 'BLENDED' if blending else 'DITHERED'),
                        ('blend_method', 'BLEND' if blending else 'HASHED')):
        try:
            setattr(m, attr, value)
        except (AttributeError, TypeError):
            pass
    if lit:
        nt.links.new(comb_c, bsdf.inputs['Base Color'])
        nt.links.new(comb_a, bsdf.inputs['Alpha'])
        nt.links.new(bsdf.outputs[0], out.inputs['Surface'])
    else:                    # unlit: the combined color as is
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
    return next((n for n in m.node_tree.nodes if n.type == 'TEX_IMAGE'), None)


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
                                            '(Scene > N64 Fighter)'),
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
    bl_label = 'N64 Fighter'
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'material'
    # first in the Material tab: header-less panels (like Blender's material slots) always
    # come before the others; among them, the lowest order first
    bl_order = -100
    bl_options = {'HIDE_HEADER'}

    @classmethod
    def poll(cls, context):
        return context.object is not None or context.material is not None

    def draw(self, context):
        lay = self.layout
        lay.label(text='N64 Fighter', icon='MATERIAL')
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


class N64_PT_scene(bpy.types.Panel):
    bl_label = 'N64 Fighter'
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'scene'

    def draw(self, context):
        lay = self.layout
        lay.use_property_split = True
        lay.prop(context.scene, 'n64ui_light', text='Light Color')
        lay.prop(context.scene, 'n64ui_shadow', text='Shadow Color')


CLASSES = (N64_OT_texture_add, N64_OT_texture_remove, N64_OT_state_add, N64_OT_state_edit, N64_OT_state_remove, N64_PT_material, N64_PT_scene)


def _collapse_outliner():
    """Once, on the first open of a generated .blend: every collection collapsed."""
    scene = bpy.context.scene
    if scene is None or not scene.get('n64_collapse_outliner'):
        return None
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type != 'OUTLINER':
                continue
            region = next((r for r in area.regions if r.type == 'WINDOW'), None)
            with bpy.context.temp_override(window=win, area=area, region=region):
                for _ in range(4):
                    try:
                        bpy.ops.outliner.show_one_level(open=False)
                    except RuntimeError:
                        break
    scene['n64_collapse_outliner'] = False
    return None


def register():
    for name, prop in {**PROPS, **ADV_PROPS}.items():
        setattr(bpy.types.Material, name, prop)
    for name, prop in SCENE_PROPS.items():
        setattr(bpy.types.Scene, name, prop)
    for c in CLASSES:
        try:
            bpy.utils.register_class(c)
        except ValueError:                 # already registered (add-on + embedded copy)
            pass
    if not bpy.app.background:
        bpy.app.timers.register(_collapse_outliner, first_interval=0.5)
    # picking / opening an image on a texture node refreshes the N64 view
    bpy.msgbus.subscribe_rna(key=(bpy.types.ShaderNodeTexImage, 'image'), owner=_MSGBUS_OWNER, args=(),
                             notify=_image_changed)


def unregister():
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


if __name__ == '__main__':
    register()
