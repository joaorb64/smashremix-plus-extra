"""In Blender: replace a .blend's own copy of the N64 panel (the n64_panel.py text, run when the file
opens) with the tools' current n64_panel.py, and save. Nothing else in the file changes.

  blender -b model.blend --python update_panel.py -- OUT_DIR    (ssb64_model.py update-panel OUT_DIR)
"""
import os

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
src = os.path.join(HERE, 'n64_panel.py')
txt = bpy.data.texts.get('n64_panel.py') or bpy.data.texts.new('n64_panel.py')
old = txt.as_string()
txt.from_string(open(src, encoding='utf-8').read())
txt.use_module = True
for sc in bpy.data.scenes:
    if 'n64_export_dir' in sc.keys() or 'fighter_export_dir' in sc.keys():
        sc['n64_tools_dir'] = HERE
bpy.ops.wm.save_mainfile()
print('panel', 'unchanged' if old == txt.as_string() else 'updated', 'in', bpy.data.filepath)
