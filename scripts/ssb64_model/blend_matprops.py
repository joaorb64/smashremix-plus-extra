"""(in Blender) A material's N64 settings from its export manifest entry: the n64_* custom
properties import_fighter.py reads and n64_panel.py draws (format, wrap, culling, lighting,
own light colors, filter, render state) and its flat / costume colors. Shared by build_blend.py
(the model's materials) and blend_reference.py (the reference items and opponent)."""


def set_costume_lights(m, colors):
    """An MObj's own light colors for this costume (vanilla Mario's shoes are white, lit
    by a brown light). Shown in Blender only (n64_costume_light / n64_costume_shadow):
    the importer keeps the MObj's colors from the manifest."""
    for key, track in (('n64_costume_light', 'light1'), ('n64_costume_shadow', 'light2')):
        if colors.get(track):
            m[key] = colors[track][:6]
        elif key in m:
            del m[key]


def set_base_color(m, hex_rgba):
    srgb = [int(hex_rgba[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in srgb]
    for node in m.node_tree.nodes:
        if node.type == 'BSDF_PRINCIPLED':
            node.inputs['Base Color'].default_value = (*lin, 1.0)
    m.diffuse_color = (*lin, 1.0)                 # solid view color


def apply(m, entry):
    """Custom properties (Material > Custom Properties) that import_fighter.py reads:
      n64_format   CI4 / CI8 / IA4 / IA8 / IA16 / I4 / I8 / RGBA16
      n64_wrap_s / n64_wrap_t   repeat / mirror / clamp (per axis)
      n64_cull     back / front / none / both (back-face culling; game default: back)
      n64_lighting on / off (off: vertex colors instead of lighting, e.g. effects)
      n64_light_color / n64_shadow_color  this material's own light colors (hex RGB),
                   instead of the model's (scene properties of the same names)
      n64_state    the material's render state, "w0 w1; w0 w1; ..." (blend mode,
                   combiner, prim/env colors): copy one from another material to
                   get its look (e.g. an alpha-blended effect)"""
    if entry and entry.get('kind') in ('mobj', 'tex', 'prim'):
        if not entry.get('untextured') and entry.get('kind') != 'prim':
            wrap = entry.get('wrap') or {}
            m['n64_format'] = entry.get('format', 'CI4')
            m['n64_wrap_s'] = wrap.get('s', 'clamp')
            m['n64_wrap_t'] = wrap.get('t', 'clamp')
            for a in 'st':          # tile clamped past the texture's size (vanilla fighters)
                if wrap.get(f'clamp_{a}'):
                    m[f'n64_clamp_{a}'] = float(wrap[f'clamp_{a}'])
        state = entry.get('state') or []
        cull = next((int(b, 16) for a, b in state if a.startswith('D9')), 0x400)
        m['n64_cull'] = {0: 'none', 0x400: 'back', 0x200: 'front', 0x600: 'both'}.get(cull & 0x600, 'back')
        m['n64_lighting'] = 'on' if cull & 0x20000 else 'off'
        m.use_backface_culling = m['n64_cull'] in ('back', 'both')       # viewport shows it too
        own = {a: b for a, b in state if a.startswith('DB0A')}           # its own light colors
        if 'DB0A0000' in own:
            m['n64_light_color'] = own['DB0A0000'][:6]
        if 'DB0A0018' in own:
            m['n64_shadow_color'] = own['DB0A0018'][:6]
        filt = next((b for a, b in state if a == 'E3001201'), None)
        if filt is not None:
            m['n64_filter'] = {0: 'point', 0x2000: 'bilinear', 0x3000: 'average'}.get(int(filt, 16), 'bilinear')
        m['n64_state'] = '; '.join(f'{a} {b}' for a, b in state if not a.startswith(('D9', 'DB', 'E3001201', 'E3001001')))
    if entry and entry.get('untextured') and m.node_tree and (entry.get('costume_colors') or [{}])[0].get('prim'):
        set_base_color(m, entry['costume_colors'][0]['prim'])
    if entry and entry.get('costume_colors'):
        set_costume_lights(m, entry['costume_colors'][0])
    elif entry and entry.get('kind') == 'prim' and m.node_tree and entry.get('color'):
        # one DL prim color, no MObj: the glTF import leaves Blender's default gray, and
        # import_fighter.py reads the base color back as the prim color
        set_base_color(m, ''.join(f'{v:02X}' for v in entry['color'][:3]))
