# fighter_model: edit fighter, item, projectile and stage models in Blender

Export a fighter's model (body, low detail, alternate parts, every costume,
facial expressions, palettes, materials) to an editable .blend, then build the
edited .blend back into its `main.bin` / `character.bin`. Items and projectiles
(one model described by ITAttributes / WPAttributes) and stages (their geometry
layers) work the same way, see [Items and projectiles](#items-and-projectiles) and
[Stages](#stages).

Status: export and import work. Unedited round trips of Meta Knight, Mega Man and
Snake render identically (same textures in every costume, same expressions/palettes,
same MObj order); so do items and projectiles (rebuilt model files have no leftover
data). ROM export reads Remix fighters (from `File.asm`) and vanilla fighters and
items, including vanilla display-list features (shifted / scaled texture tiles,
mirrored clamped tiles, vertex edits across joints, shared files). Not yet: writing
items back into the vanilla shared item file, editing stage animations in Blender
(they're kept), adding model-part variants,
build-process wiring (planned: .blend files in the repo, compiled at build).

Blender commands are given twice: as `blender`, and for Blender installed from
Flatpak (`flatpak run org.blender.Blender`, with absolute paths: Flatpak Blender
can't see /tmp). `--factory-startup` keeps your installed add-ons out of it (some
print errors when Blender runs without a window); the glTF add-on it needs is built in.
The glTF exporter's "Vertex Color alpha without color" warnings are harmless.

## 1. Export

```
python3 appender/scripts/fighter_model/export_fighter.py extra_characters/<Name> OUT_DIR
```
To export a Remix fighter directly from `original_extra.z64`, select it by the
constant name from `appender/smashremix/src/File.asm`:
```
python3 appender/scripts/fighter_model/export_fighter.py --rom appender/smashremix/roms/original_extra.z64 --fighter FALCO OUT_DIR
```
Paired `*_MAIN` and `*_CHARACTER` IDs are discovered from `File.asm`.
Named fighters default to four costumes; override with `--costumes N` when needed.
Vanilla fighters are also available by name (for example `MARIO`); their IDs
are the original US 1.0 file-table IDs, so the selected ROM must contain those
files (`original.z64` or `original_extra.z64`).
For another Remix fighter pair, provide its hexadecimal table IDs instead:
```
python3 appender/scripts/fighter_model/export_fighter.py --rom ROM.z64 --main-file-id 0xCB --char-file-id 0x128 OUT_DIR
```
ROM file IDs can be found in `appender/scripts/gen_editable_rom.py`. ROM exports
read relocation-chain offsets from the ROM file table; ordinary `main.bin` /
`character.bin` exports continue using their config offsets. Data a ROM file takes
from other files (its external relocation chain, e.g. the bone texture every
vanilla electric skeleton shares) is read too.

### Items and projectiles

```
python3 appender/scripts/fighter_model/export_fighter.py FOLDER OUT_DIR --object NAME
```
FOLDER is a character or stage folder; NAME is found in its config.yaml: the
attributes file `NAME_hitbox` (`files:`) and the model file `NAME_gfx`
(`append_files:`, projectiles) or `NAME` (`files:`, stage items). Chain heads come
from those entries; the attributes are at the attributes file's external chain head
(`--attr-offset` to override). Other files: `--attr X_hitbox.bin --model X.bin
--attr-heads INT,EXT --model-head HEAD [--attr-offset OFF]`. Vanilla common items:
`--rom ROM.z64 --item HAMMER` (see `VANILLA_ITEMS` in export_fighter.py).

The model is a DObj tree or a bare display list (sprite projectiles: one quad);
the .blend has one costume. Materials with several images (e.g. damage stages a
hazard swaps in) are drawn in `previews/variants.png`. Items fade toward the env
color by its alpha (flashing), which the game keeps at 0: the preview and Blender
draw them with env 00000000.

### Stages

```
python3 appender/scripts/fighter_model/export_fighter.py STAGE_DIR OUT_DIR --stage
```
Reads the geometry layers of `header.bin`'s ground data (MPGroundData at 0x14:
`gr_desc[0..3]`, each a DObj tree in `stage.bin` with its materials and material
scripts) using config.yaml `offsets:`. Meshes are `L<n>_j<joint>`, bones
`L<n>_joint_NN` (one armature for every layer). Each `gr_desc` is a stage type/layer;
its DObj joints (rooms) are the meshes `L<n>_j<joint>` in its `Layer <n> - <type>` collection. Layer 0 is the
background, layer 1 is the z-buffered stage/collision-group layer, and layers 2 and 3
are foreground. Layer 1's joints are the groups the hazard code toggles, by index.
`previews/stage.png`: the whole stage from the six sides; `previews/layers.png`: each
layer alone.

Layer types (decomp grdisplay.c), drawn in this order:

| Layer | Draw priority | Depth buffer | Use |
|---|---|---|---|
| 0 | 4 (first) | off: painted in display-list order | background |
| 1 | 6 (with the fighters) | on | the z-buffered stage, collision groups |
| 2 | 13 | off | drawn over everything |
| 3 | 17 (last) | off | drawn over everything |

Within a layer the rooms (joints) and each display list's materials draw in order; the
export records it (manifest objects' `draw_order`) and the import keeps it. The preview
draws exactly that; Blender can't (layers 2 / 3 are shown In Front, layer 0 is depth
tested there).

Animations (the layers' anim_joints scripts, decomp objanim.c) are played and sampled
every frame into the glTF animation `Stage`: the `Stage` armature's action, 60 fps, the
timeline covering one loop (capped at 30 s for scripts the stage code drives, e.g.
moving platforms). They're for viewing: the import keeps the original scripts as they
are. Vanilla stages are available by name too (`DREAMLAND`, `CASTLE`, `SECTOR_Z`...:
`VANILLA_MAPS` in export_fighter.py).

To export a stage directly from a Smash Remix ROM, give its `File.asm` header name:
```
python3 appender/scripts/fighter_model/export_fighter.py --rom ROM.z64 --map=BATTLEFIELD OUT_DIR
```
Use a stage name such as `BATTLEFIELD`, `DEKU`, or `MUTE` (the `STG_` prefix and
`_HEADER` suffix are optional), or the Map file's hexadecimal ID. The ROM supplies
the stage header and its referenced geometry files. `--map BATTLEFIELD` works too;
the equals form is useful when keeping option/value pairs together in scripts.

```
blender -b --factory-startup --python appender/scripts/fighter_model/build_blend.py -- OUT_DIR
```
Flatpak:
```
flatpak run org.blender.Blender -b --factory-startup --python $PWD/appender/scripts/fighter_model/build_blend.py -- $PWD/OUT_DIR
```
`--main`/`--char` export other files; `--costumes N` overrides config.yaml's
`num_costumes`. Writes previews to `OUT_DIR/previews/` (`--no-preview` to skip;
`preview.py OUT_DIR` renders them again), all with the model's normals, light /
shadow colors and per-material culling:

| Sheet | What |
|---|---|
| `body.png` | every costume from the front, left, back, right, top, bottom |
| `expressions.png` | every image of each material that has several (facial expressions), on the model |
| `parts.png` | every model part from the six sides; one row per costume when its colors change per costume |
| `skeleton.png` | the electric-shock skeleton model(s) |
| `variants.png` | items / projectiles: every image of each material that has several (instead of expressions.png) |

`OUT_DIR/fighter.blend` contains:

| Collection | What |
|---|---|
| Costume 0 .. N-1 | the hi-detail body once per costume, all at the same place (only Costume 0 shown: toggle the collections' visibility). All costumes share the same meshes (linked duplicates): edit the geometry once, every costume updates. Materials are per costume (linked to the object). |
| Low detail | lo-detail body (hidden) |
| Model parts | alternate pieces per joint and variant: hands (open/fist...), held props, effects. Objects `part<variant>_<hi\|lo>_j<joint>` (hidden) |
| Electric skeleton | the model drawn while electrocuted, `skel<id>_j<joint>` (hidden). It's drawn with the joint's body materials (MObjs) or its own inline textures (usually the shared skeleton texture) |
| Armature | the skeleton, bones `joint_00`.. (shared). Shown as sticks, in front, with names; bones reach toward their child joints, joints stacked on one spot (separate rotation axes) are short stubs, and bone collections Left (red, the fighter's left: +X) / Right (blue) / Center (yellow) group them. Display only: the import takes the joints from the game file |
| Stage types / rooms | `Layer 0 - Background`, `Layer 1 - Stage (Z-buffered)`, and `Layer 2/3 - Foreground`; each contains `Room <joint>` collections for its DObj joints. Moving a room to another layer changes its stage type. |

Textures are external files (`textures/`, relative paths): keep the export folder
together. Judge colors in Material Preview: it runs the N64 setup (combiner, wrap
/ mirror / clamp per axis, per-material light colors); Solid view has one wrap mode
per texture and no combiner.

Identical materials (e.g. the hi and lo models' copies) are exported once, so an
edit applies everywhere they're used. Vertex normals are the model's own (custom
normals); after reshaping, Mesh > Clean Up > Clear Custom Split Normals Data lets
Blender compute them. Scene custom properties `n64_light_color` / `n64_shadow_color`
(hex RGB) are the model's light and ambient (shadow) colors.

### Textures (`OUT_DIR/textures/`, referenced relatively from the .blend)

| File | What | Edit it to |
|---|---|---|
| `<mat>_c<N>.png` | costume N as it looks in game (what the Costume N material shows) | paint costume N in Blender: a recolor of the same drawing changes only costume N's palette; a repaint changes the image (shared by the costumes using it) |
| `<mat>_img<i>.png` | image list entry i (facial expressions, alternate textures), drawn with costume 0's palette | redraw an expression. CI images keep their palette's colors (new colors map to the nearest one) |
| `<mat>_pal<p>.png` | palette list entry p, shown on the costume-0 image | recolor: each pixel's color becomes that palette slot's color |

The img/pal PNGs are loaded in the .blend as images (Image Editor) to paint.
Unedited entries keep their exact original bytes. PNG dimensions follow the
texture's render-tile masks; the render-tile extents are kept separately so
rebuilding preserves repeated or clamped sampling.

### Material settings (Blender: Material > N64 Fighter)

The **N64 Fighter** panel is the first thing in the Material tab, and all a material
needs: the object's material slots, the texture (pick, open or create its image, or
remove it for a flat color with a color picker), dropdowns, toggles and color pickers
with a description on each option, including Render Mode (game / cut-out / translucent
/ glow...) and Combiner presets (the model's light / shadow colors are in the Scene
tab), and a **Display List Commands** list: the material's extra commands
(render mode, color combiner, prim / env / blend colors, alpha compare...) decoded
in words, with color pickers, edit / remove buttons and an Add menu of presets (or a
raw command). The .blend opens in Material Preview, where every material is drawn
through its real N64 color combiner (as nodes: prim / env colors, texture, vertex
colors when unlit, blending), wrap modes and texture filtering, so it looks like the
game; changing a setting updates the view. It's embedded in the .blend: allow the file's scripts when Blender asks
(or Text Editor > `n64_fighter_panel.py` > Run Script). Or install
`n64_fighter_panel.py` once as an add-on (Edit > Preferences > Add-ons > Install from
Disk). The panel only edits these custom properties, which work without it too
(Material > Custom Properties):

| Property | Values |
|---|---|
| `n64_format` | `CI4`, `CI8`, `IA4`, `IA8`, `IA16`, `I4`, `I8`, `RGBA16` (changing it rebuilds the material from the costume images) |
| `n64_filter` | `bilinear` (the game's default), `point`, `average`: texture filtering |
| `n64_wrap_s`, `n64_wrap_t` | `repeat`, `mirror`, `clamp`, per axis (X / Y) |
| `n64_lighting` | `on`, `off`. Off (effects): the vertex colors shade it instead of the lights (Blender's color attribute) |
| `n64_light_color`, `n64_shadow_color` | this material's own light / shadow (ambient) colors, hex RGB (else the model's: scene properties of the same names) |
| `n64_cull` | `back`, `front`, `none`, `both`: face culling (the original's; new materials: `back`, the game's default). The viewport's Backface Culling shows it |
| `n64_state` | the render state, `"w0 w1; w0 w1; ..."` hex pairs: blend mode (E2), combiner (FC), prim / env colors (FA / FB). Copy it from another material to get its look (e.g. Meta Knight's tornado: an IA8 texture, alpha blended, tinted by the prim color) |

Transparency: CI textures are transparent where a palette color's alpha is 0;
IA / RGBA textures carry alpha (blended with a blending `n64_state`, else cut out).

Cloaking device: the game sets the fighter's render mode (opaque, or translucent
while cloaked / fading) before each joint's display list; a display list that sets
its own render mode overrides it, which is what config.yaml `cloaking_fix` patches
at runtime. Built models don't need it: cut-out materials (1-bit alpha: CI, RGBA16,
IA4) don't set a render mode. They set only the CVG_X_ALPHA bit of it (coverage x
texture alpha: the cut-out of the game's TEX_EDGE modes) plus alpha compare, and
leave the rest of the game's mode alone, so they fade with the rest of the fighter.
Only real blending effects (e.g. IA8 smoke) keep a render mode; they're drawn last
in their display list (the game resets the mode for the next joint).
`--keep-render-modes` keeps the original render modes. The import says which
`cloaking_fix` entries to remove (or where the commands moved).

Untextured materials: their color is the material's Base Color, per costume (Costume N
materials), e.g. Knuckles' body. It changes per costume through the MObj's costume
script (prim color events), or is a fixed prim color in the display list.

New materials made in Blender become CI4 (16 colors per costume image,
reduced if there are more), with a palette per costume.

### Modeling new things

- **Geometry**: add or edit meshes in `Costume 0` (and `Low detail` for the far /
  low-poly model, which is built separately). Every vertex belongs to one bone
  (`joint_NN`, weight 1: parent to the armature, Armature modifier, a vertex group
  named after the bone). A triangle may join two bones (e.g. a knee), not three.
- **Costumes**: a new object in `Costume 0` is drawn in every costume. For it to look
  different per costume, give the other costumes their own material: a linked
  duplicate (Alt+D) in each `Costume N` collection, at the same place, with its
  material slot linked to the object.
- **Textures**: any material with an Image Texture becomes a new texture. CI4 by
  default (16 colors per costume image; more are reduced): `n64_format` for others.
  Sizes: powers of two (or rows of a multiple of 8 bytes), and it must fit the
  texture memory (palette formats get half, the palette uses the rest): CI4 up to
  4096 texels (64x64), CI8 2048 (64x32), I4 / IA4 8192 (128x64), I8 / IA8 4096
  (64x64), RGBA16 / IA16 2048 (64x32). Wrap: `n64_wrap_s` / `n64_wrap_t` (default clamp).
  Transparency: pixels with alpha below 50% (cut out, cloaking-safe).
- **Flat colors**: a material without a texture uses its Base Color (per costume
  through the `Costume N` materials).
- **Culling**: the material's Backface Culling checkbox (Material > Settings): on =
  back faces culled (the game's default), off = double-sided. `n64_cull` = `front` or
  `both` for the rare other cases. Faces point where Blender's normals point.
- **Lighting**: lit by default; `n64_lighting` = `off` for effects colored by the
  vertex colors (Color Attribute) instead.
- **Normals**: imported models carry their original normals (custom normals); new
  meshes use Blender's. After reshaping existing ones: Mesh > Clean Up > Clear Custom
  Split Normals Data.

## 2. Import

```
blender -b --factory-startup OUT_DIR/fighter.blend --python appender/scripts/fighter_model/blend_to_gltf.py -- WORK_DIR
```
Flatpak:
```
flatpak run org.blender.Blender -b --factory-startup $PWD/OUT_DIR/fighter.blend --python $PWD/appender/scripts/fighter_model/blend_to_gltf.py -- $PWD/WORK_DIR
```
```
python3 appender/scripts/fighter_model/import_fighter.py extra_characters/<Name> WORK_DIR --out BUILD_DIR [--preview PREVIEW_DIR]
```
- The base `main.bin` / `character.bin` (`--main`/`--char`, default the character
  folder's) must be the ones that were exported (checked): original texture data
  is read from them. Everything that isn't the model is kept.
- Rebuilt: hi / lo body, model parts, electric skeleton, materials (MObjSubs in their original order:
  texture parts and code address them by index), image / palette lists (same
  lengths and order), costume scripts.
- character.bin: the old model is removed first, then the new one appended;
  identical vertex buffers, display lists, images, palettes, materials and costume
  scripts are stored once. Must stay under 0x3FFFC bytes (16-bit word offsets).
- main.bin / reqlist: existing pointers are re-pointed. A pointer main.bin didn't
  have (e.g. a part that had no material list) is added to its external chain,
  and `main_reqlist.txt` gets a `${CHARACTER}` line for it, in the same order
  (before the `extend_main_reqlist` lines). Unedited round trips add none.
- `cloaking_fix` in config.yaml points at render-mode commands by byte offset in a
  joint's display list; the import prints the new offsets when they moved.
- `--preview DIR`: renders what was built (read back from the new files) into DIR.

Sizes (unedited round trip): Meta Knight 75,296 -> 55,088 bytes (its hi and lo
models were duplicate data), Mega Man 107,632 -> 115,360, Snake 180,748 -> 107,800.

### Items and projectiles

```
python3 appender/scripts/fighter_model/import_object.py FOLDER NAME WORK_DIR [--out OUT_DIR]
```
- The model file (`NAME_gfx.bin` / `NAME.bin`) is written from scratch: the new
  model, plus whatever the attributes reach that isn't the model (anim_joints) -
  nothing else from the old file (GoldEditor exports carry unused objects and
  textures; a re-import drops them, so they need no trimming at build).
- Kept as the original had it: DObj tree or bare display list, DObjDLLink entries
  (GoldEditor models), MObj image lists (in order: code may swap their entries).
- `NAME_hitbox.bin`: its pointers into the model file are re-pointed (a new one is
  added to its external chain, with a line in `NAME_hitbox_reqlist.txt`).
- config.yaml: the model file's chain head is updated (it changes with the new
  layout) and a `trim:` option on that entry is removed. With `--out`, nothing in
  FOLDER is touched and the new head is printed instead.
- Stage builds derive the `FILES.<NAME>.*` offsets (mobjsub, sprites, sprite0..)
  from the files, so they follow the new layout.
- Material scripts (texture animations) are kept as they are, and so is each display
  list's original end state (render mode restored...: items drawn with no render mode
  of their own, like stage hazards, inherit what a model leaves set).

### Stages

```
python3 appender/scripts/fighter_model/import_stage.py STAGE_DIR WORK_DIR [--out OUT_DIR]
```
`blend_to_gltf.py` writes `layer<N>.gltf` per layer collection. `stage.bin` is written
from scratch: the rebuilt layers plus everything else `header.bin` points at in it
(collision geometry, layer animations), and the layers' material scripts (texture
animations), unchanged. Each layer keeps its DObjDesc array (transforms and order:
collision groups and hazard code address joints by index); a layer with no meshes in
the .blend keeps its joints without display lists. `header.bin`'s pointers are
updated (extra `header_reqlist.txt` lines after the pointer lines, e.g. imported item
files, stay last) and config.yaml `offsets: stage` gets the new chain head. The
collision tools (`stage_collision.py`) read the rebuilt file the same way.

## What is read (through main.bin's fighter attributes, decomp fttypes.h FTAttributes)

- common parts (hi/lo): DObjDesc skeleton, display lists, MObjSub materials and
  costume scripts. Joints that reuse their parent's cached vertices (knees,
  elbows, neck...) are drawn with one shared vertex cache, like the game.
- model parts: per joint `[variant][hi, lo]` FTModelPart (dl, mobjsubs, costume
  scripts).
- texture parts: facial expressions (joint + MObj index whose image swaps;
  moveset event 0x2B sets the image index).
- textures: CI4/CI8, RGBA16/32, IA4/8/16, I4/8; loaded inline (SETTIMG in the
  DL) or from the MObj's lists; wrap modes and render state per material.
- MObj flags (decomp objdisplay.c): 0x01 image from the list, 0x04 palette from
  the list. With 0x04 alone the MObj only points at the palette; the DL loads it.

- electric skeleton: attr->skeleton[id][joint] {dl, flags}, ids 1..3.
- items / projectiles: ITAttributes / WPAttributes +0x0 data (DObjDesc array or a
  display list), +0x4 p_mobjsubs, +0x8 anim_joints (kept), +0xC p_matanim_joints.
- vanilla display-list features: tile upper-left (G_SETTILESIZE, subtracted from
  the UVs) and G_TEXTURE scale; tiles wider than their texture with mirror + clamp
  (clamped at the extent first, then mirrored by the mask: manifest wrap
  `clamp_s` / `clamp_t`); G_MODIFYVTX texture coordinates / colors of vertices
  shared with the parent joint; a prim color set after an MObj's material (the
  MObj no longer applies); texels past a clamped extent (never drawn) are shown as
  the edge texel in the costume PNGs.
- joints: DObjDesc entry i is the game's joint i + 4 (player joints[0..3] are
  TopN/TransN/XRotN/YRotN; joints[] starts at 0x8E8 in the player struct).

Not handled yet (passed through untouched): fighter DObjDLLink arrays (and two-DL
skeleton entries), accessory parts, hidden parts.
