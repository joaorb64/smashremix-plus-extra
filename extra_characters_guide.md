Ryu/
- animations/...bin
- moveset/...bin
- config.yaml
- main.bin
- character.bin
- main_reqlist.txt -> For all lines that reference the original MAIN file, replace with ${CHARACTER}
- character_reqlist.txt
- main.asm

# CONFIG.YAML
# Example:

# File offsets for main and character
offsets:
  main: ["0398", "0000"]
  character: ["0004", "AB7C"]
# Character definitions
definitions:
  base_character: LINK

# ANIMATIONS
Animations will be imported as CHARACTER_ANIM_FILENAME. Example: Ryu/animations/idle.bin -> File.RYU_ANIM_IDLE

# ASM
main.asm will be compiled into

scope Ryu {
    insert filename "moveset/filename.bin"
    ...for all files in moveset

    // MAIN.ASM FILE CONTENT
}

This means that you'll have to adapt your main file where:
- Animation files are named File.CHARNAME_ANIM_FILENAME (like in the File.RYU_ANIM_IDLE example)
- Moveset files are named "filename". So if you have "Jab1.bin", reference it as Jab1.
# SERIES LOGO
One logo mesh makes the 3D logo of the results / data screens, the CSS logo
(series_logo.png) and the HUD emblem (hud.png). Put in the character folder one of:
- series_logo.obj (Wavefront OBJ)
- series_logo.gltf / series_logo.glb (glTF 2.0)
- series_logo.bin: a GEE export of file 0x23 (legacy; only its triangles are used)

The logo is Y up and faces +Z (the front view; Blender's OBJ / glTF importers
put it facing Blender's front camera). Units don't matter: the build centers
it and scales its longest side to 300 game units, the size of the vanilla
logos. Polygons are triangulated by the build; materials, UVs and colors are
ignored: the 3D logo always gets the vanilla emblem material (player colors).
Only faces count: loose edges are ignored, like the Reference_frame square in
the template / converted OBJs (the box the logo is fitted into; it can't go
offscreen, but a wide or tall logo ends up smaller in the other direction).

series_logo.png / hud.png are generated only when the folder has no hand-made
PNG; the CSS one is also skipped when definitions.series_logo_css is set. To
turn generation off:

series_logo:
  css: false
  hud: false

Tools (appender/scripts/series_logo.py):
- `convert extra_characters/<Name> [--remove-bin]`: series_logo.bin -> series_logo.obj
- `new extra_characters/<Name>`: series_logo.obj from the template (the vanilla Mario logo)
- `render extra_characters/<Name> OUT_DIR`: preview the generated
  series_logo.png, hud.png and 3D block.
