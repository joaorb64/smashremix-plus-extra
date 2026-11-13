# ssb64_model: edit Smash 64 models in Blender

Fighters, items, projectiles and stages, from the repo's folders or from a ROM:

```
python3 appender/scripts/ssb64_model/ssb64_model.py export extra_characters/<Name> OUT_DIR          # -> OUT_DIR/model.blend
# ... edit OUT_DIR/model.blend in Blender ...
python3 appender/scripts/ssb64_model/ssb64_model.py import OUT_DIR                                  # -> back into extra_characters/<Name>
```

| Export | What |
|---|---|
| `export extra_characters/<Name> OUT_DIR` | a fighter (main.bin + character.bin) |
| `export extra_stages/<Name> OUT_DIR` | a stage's geometry layers (header.bin + stage.bin) |
| `export FOLDER NAME OUT_DIR` | the folder's item / projectile NAME (config.yaml `NAME_hitbox` + `NAME_gfx` / `NAME`) |
| `export ROM.z64 NAME OUT_DIR` | a fighter (`MARIO`, `FALCO`...), stage (`DREAMLAND`, `BATTLEFIELD`, or the header file's hex ID) or common item (`HAMMER`...) by name; `--kind fighter\|stage\|object` when a name is several |

`export` writes the model (glTF, textures, `manifest.json`, `previews/`) and runs Blender to build
`model.blend` (`--no-blend` skips it). `import` runs Blender to read the .blend back
(into `OUT_DIR/import_work/`) and writes the files to the folder the export came from (it's in
`manifest.json`): give the folder after OUT_DIR for a ROM export or another folder, `--out DIR` to
write elsewhere (the folder and its config.yaml stay untouched; the new chain heads are printed).
`blend OUT_DIR` rebuilds only the .blend, `preview OUT_DIR` only the previews. `--help` lists the
options; arguments after `--` go to the scripts below as they are.

Blender: `--blender CMD`, else `$BLENDER`, else `blender` on the PATH, else the Flatpak
(`org.blender.Blender`; it can't see /tmp, keep OUT_DIR elsewhere).

The steps are scripts of their own, for doing one at a time: `export_model.py` (game files ->
glTF + manifest), `build_blend.py` (in Blender: -> model.blend), `blend_to_gltf.py` (in Blender:
model.blend -> work folder), `import_fighter.py` / `import_object.py` / `import_stage.py` (work
folder -> game files), `preview.py`.


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
items back into the vanilla shared item file, adding model-part variants,
build-process wiring (planned: .blend files in the repo, compiled at build).

Blender runs with `--factory-startup` (installed add-ons stay out of it; the glTF add-on
it needs is built in). The glTF exporter's "Vertex Color alpha without color" warnings are
harmless.

## 1. Export

```
python3 appender/scripts/ssb64_model/ssb64_model.py export extra_characters/<Name> OUT_DIR
```
To export a Remix fighter directly from `original_extra.z64`, select it by the
constant name from `appender/smashremix/src/File.asm`:
```
python3 appender/scripts/ssb64_model/ssb64_model.py export appender/smashremix/roms/original_extra.z64 FALCO OUT_DIR
```
Paired `*_MAIN` and `*_CHARACTER` IDs are discovered from `File.asm`.
Named fighters default to four costumes; override with `--costumes N` when needed.
Vanilla fighters are also available by name (for example `MARIO`); their IDs
are the original US 1.0 file-table IDs, so the selected ROM must contain those
files (`original.z64` or `original_extra.z64`).
For another Remix fighter pair, provide its hexadecimal table IDs instead:
```
python3 appender/scripts/ssb64_model/export_model.py --rom ROM.z64 --main-file-id 0xCB --char-file-id 0x128 OUT_DIR
python3 appender/scripts/ssb64_model/ssb64_model.py blend OUT_DIR
```
ROM file IDs can be found in `appender/scripts/gen_editable_rom.py`. ROM exports
read relocation-chain offsets from the ROM file table; ordinary `main.bin` /
`character.bin` exports continue using their config offsets. Data a ROM file takes
from other files (its external relocation chain, e.g. the bone texture every
vanilla electric skeleton shares) is read too.

### Items and projectiles

```
python3 appender/scripts/ssb64_model/ssb64_model.py export FOLDER NAME OUT_DIR
```
FOLDER is a character or stage folder; NAME is found in its config.yaml: the
attributes file `NAME_hitbox` (`files:`) and the model file `NAME_gfx`
(`append_files:`, projectiles) or `NAME` (`files:`, stage items). Chain heads come
from those entries; the attributes are at the attributes file's external chain head
(`-- --attr-offset OFF` to override). Other files: `export_model.py OUT_DIR --attr X_hitbox.bin
--model X.bin --attr-heads INT,EXT --model-head HEAD [--attr-offset OFF]`. Vanilla common items:
`export ROM.z64 HAMMER OUT_DIR` (see `VANILLA_ITEMS` in export_model.py).

The model is a DObj tree or a bare display list (sprite projectiles: one quad);
the .blend has one costume. Materials with several images (e.g. damage stages a
hazard swaps in) are drawn in `previews/variants.png`. Items fade toward the env
color by its alpha (flashing), which the game keeps at 0: the preview and Blender
draw them with env 00000000.

### Stages

```
python3 appender/scripts/ssb64_model/ssb64_model.py export STAGE_DIR OUT_DIR
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
draws exactly that, and so do Blender renders (F12): each layer is its own view layer
(`Stage Layer <n>`), composited in that order (the compositor's `N64 Stage Layers`). The
viewport can't composite view layers: it shows every layer depth-tested together (the
`ViewLayer` view layer, for editing; it isn't rendered).

Animations (the layers' anim_joints scripts, decomp objanim.c) are played and sampled
every frame into the glTF animation `Stage`: the `Stage` armature's action, 60 fps. A
looping joint has one cycle of keys with a Cycles modifier, at its own length (joints of
one stage loop independently, e.g. 300 and 500 frames); a script that never loops or ends
(the stage code drives it, e.g. moving platforms) is sampled for 30 s. Material
animations loop the same way (cycles up to 16384 frames are found). Both are editable:
see [Joint animations](#joint-animations) and [Material animations](#material-animations).
Vanilla stages are available by name too (`DREAMLAND`, `CASTLE`, `SECTOR_Z`...:
`VANILLA_STAGES` in export_model.py).

To export a stage directly from a Smash Remix ROM, give its name:
```
python3 appender/scripts/ssb64_model/ssb64_model.py export ROM.z64 BATTLEFIELD OUT_DIR
```
Use a stage name such as `BATTLEFIELD`, `DEKU`, or `MUTE` (`File.asm`'s
`*_HEADER` constants (with or without the `STG_` prefix); the prefix and suffix
are optional;
`export_model.py --help` lists them all), or the stage header file's hexadecimal ID (the decomp calls
it the Map file). The ROM supplies the stage header and its referenced geometry files.

### Hurtboxes (fighters)

The `Hurtboxes` collection (hidden at first: its eye in the outliner, or **Show Hurtboxes** in the
bone panel): one cube per hurtbox (decomp `FTDamageCollDesc`, attributes + 0x104, up to 11),
`hurtbox <joint> <placement>`, parented to its joint's bone, colored by placement (low blue, mid
yellow, high red). The cube is the box: move / scale it along the joint's axes (a box can't turn
against its joint; a rotation is ignored, with a warning).

Edit them from the bone: select a bone (Pose or Edit Mode), **Bone > N64 Hurtboxes** lists its boxes
with their placement (low / mid / high: the hit reaction), grabbable, and offset / size in the
joint's space, game units (config.yaml's numbers; `size` is the full width, the game halves it).
**Add Hurtbox** puts a new one on the bone, the select icon picks a box to drag in the viewport, X
deletes it. The same settings are in **Object > N64 Game Data** with a box selected. Another joint:
Relations > Parent Bone.

Order is automatic: the game tests the boxes in order (the first one hit gives the placement and
grabbability), and the import writes them the way vanilla fighters list them: torso (the joints
the head hangs from, hip first), head (high), arms (other mid boxes), legs (low); upper joints
first, the pair's higher joint index (right) before the lower. 64 of the ROM's 70 fighters already
follow it (not: Master Hand, the piano, Wario, Mewtwo and their metal versions); an import that
changes no box keeps the file's order.

They're read from config.yaml `attributes: hurtboxes` when it has them (the build writes them into
main.bin), else from main.bin. The import writes main.bin, and config.yaml's list when it has one
(each box keeps its comments). Unchanged values stay exactly as they were.

### Collision and stage objects (stages)

| Collection | What | Written to |
|---|---|---|
| `Collision > Group N` | one editable edge mesh per collision group (the yakumono id hazards toggle); each edge has its own type and flags. A saved Geometry Nodes display draws thick, type-colored edges, vertex markers, lighter drop-through edges and ledge rings; the viewport GPU overlay adds drop-through dashes when scripts are enabled. Viewport only, not in final renders. Points on the game's z = 0 plane (front view, Blender y = 0) | config.yaml `collision:` when it has it, else stage.bin |
| `Stage objects > Map objects` | empties: P1-P4 start, item spawns, rebirth platform, twister... (stage.bin map objects) | stage.bin (rebirth: config.yaml `rebirth:` when set) |
| `Stage objects > Spawn locations (default / neutral)` | config.yaml `spawn_locations` (neutral hidden) | config.yaml |
| `Stage objects > Bounds` | blast zones, camera bounds (team ones hidden): rectangles, edit their points or scale them | config.yaml when it has the key, else header.bin |

Select a `Collision > Group N` mesh and edit it like a model made of edges. In Edit Mode, select
edges or vertices and use the 3D View sidebar's **N64** tab to inspect the active selection's
current type and flags; changes apply immediately to the selected edges or vertices. Group buttons
move the whole mesh between collision groups, merging meshes when the destination already has one.
The Geometry Nodes display is saved
with the blend and the Collision collection is hidden in final renders. The game stores flags per
point and reads a segment's flags from its first point; the editor
preserves per-segment flags when it rebuilds a polyline. **Object > N64 Game Data** identifies
the selected group mesh; it also exposes settings for older line-object blends. A map object's
kind is edited there too. config.yaml blocks are rewritten only
when something changed, in their order and with their comments. A stage has at most 6 collision
groups (the Remix loader); the appender's collision writer numbers groups 1..N, so vanilla stages
with gaps in their group ids (Saffron, Mushroom Kingdom) can't have their collision rewritten.

### Joint animations

Each joint of a stage layer (bone `L<n>_joint_NN`), item or projectile (`joint_NN`: the
attributes' anim_joints, +0x8, same format) plays its own script, with its own length and
loop. Edit them like any Blender animation: pose the bone and keyframe it (the
`Stage` armature's action, 60 frames per second). **Bone > N64 Joint Animation** sets, per
joint:

- **Length** (`n64_anim_frames`): the frame it ends or starts over.
- **Loop** (`n64_anim_loop`): it starts over after its length, or stops there and holds its
  last pose. The bone's curves get / lose Cycles modifiers to match (kept in sync on every
  change, also for curves keyed later, e.g. Insert Keyframe adding location / scale), so
  Blender plays it like the game. Keys at frame 0 and at the length make Blender's cycle match (the panel
  warns otherwise).
- **Loop Start**: the frame it starts over from.
- **Game Animation From Keys** (a bone keyframed that had no animation): its length = the
  last key, looping. **Remove Joint Animation**: the joint no longer animates.

The import (`blend_to_gltf.py` -> `import_stage.py` / `import_object.py`) samples each animated bone over its
length, turns the pose back into the joint's local transform (rotation x / y / z as the
game composes it, translation, scale; mirrored, flattened and hidden joints included) and
writes its script: the original one, byte for byte, when every frame and the length / loop
are unchanged; a new one otherwise, as keyframes (linear segments and holds that give every
frame exactly; checked by playing the script) - so any Blender curve works. A joint that
moves along a translation path (TraI) keeps its original script (warned if edited).
Unchanged layers keep their anim_joints array; a layer that gains an animation gets one
(its header pointer added).

### Material animations

Stages, items and projectiles can animate their materials (an MObj's material script,
decomp objanim.c / objdisplay.c): flip through an image list (water, screens, Ryu's
hadouken), scroll the texture, change the palette or the prim / env / blend / light
colors. The export plays each script like the game and writes it as keyframes; the
import writes the script back (the original, byte for byte, when the keyframes weren't
changed). Fighters' MObj scripts are their costume scripts, not animations.

In Blender each animated value is a custom property of the material, keyframed in
the material's action (60 frames per second, like the game), shown in Material Preview
while the timeline plays. The **N64 Model** panel's **Material Animation** box lists
them; hover one + I to keyframe, **Add** for another:

| Property | Track | What |
|---|---|---|
| `n64_tex_index` | texture_id_curr | which image of the list is drawn (the `N64 Frame <i>` texture nodes) |
| `n64_pal_index` | palette_id | which palette of the list (`N64 Palette <i>` nodes) |
| `n64_offset_x` / `_y` | trau / trav | texture position, in texture widths / heights: scrolling |
| `n64_scale_x` / `_y` | scau / scav | divide the offset (with MObj flag 0x80 also the texture scale) |
| `n64_scroll2_x` / `_y` | scrollu / scrollv | the second tile's offset (two-texture combiners; not previewed) |
| `n64_tex_next` | texture_id_next | the second image (blended by the LOD fraction) |
| `n64_lod_frac` | lfrac | prim LOD fraction (combiner PRIM_LOD_FRAC) |
| `n64_prim_color`, `n64_env_color`, `n64_blend_color`, `n64_light1_color`, `n64_light2_color` | colors | RGBA, faded linearly or stepped |

- **Length / loop**: `n64_anim_frames` (the frame it ends or starts over), `n64_anim_loop`,
  `n64_anim_loop_start`. Looping curves carry a Cycles modifier: Blender repeats each
  curve's keyed span, the game repeats `n64_anim_frames`, so key frame 0 and the last
  frame for both to look the same (blend_to_gltf.py warns otherwise).
- **Keys**: Constant (holds, then steps: image / palette indices always do), Linear, and
  Bezier (written as the game's cubic segments, from the handles' slopes). The import
  plays the new script and compares every frame with Blender's curve; a curve it can't
  write as segments (other easings, Bezier handles not at a third) is written frame by
  frame, exact (and said so).
- **Image list**: the `N64 Frame <i>` nodes, in order (panel: **Add Image** copies the
  texture to a new PNG to paint; images past the original's are added to its list, CI
  ones mapped to its palette). A new material with frames gets one image per frame (CI:
  one shared palette).
- **Palette list** (CI formats): the `N64 Palette <i>` nodes, each the texture drawn in
  that palette's colors (panel: **Add Palette** copies the texture to a new PNG: recolor
  it, same drawing; the build reads the palette from it). A material with one palette
  shows none until you add one; adding the Palette property makes its texture palette 0.
- A value that isn't animated is the MObj's starting value (e.g. a fixed offset). An
  animated material always draws through an MObj: one is made for materials that had
  none, with the flags its tracks need (0x20 tile offset, 0x40 second tile, 0x200..
  colors). Removing every key removes the script.

### What the export writes

`--costumes N` overrides config.yaml's `num_costumes` (`export_model.py --main / --char`:
other files). Previews go to `OUT_DIR/previews/` (`--no-preview` to skip;
`ssb64_model.py preview OUT_DIR` renders them again), all with the model's normals, light /
shadow colors and per-material culling:

| Sheet | What |
|---|---|
| `body.png` | every costume from the front, left, back, right, top, bottom |
| `expressions.png` | every image of each material that has several (facial expressions), on the model |
| `parts.png` | every model part from the six sides; one row per costume when its colors change per costume |
| `skeleton.png` | the electric-shock skeleton model(s) |
| `variants.png` | items / projectiles: every image of each material that has several (instead of expressions.png) |

`OUT_DIR/model.blend` contains:

| Collection | What |
|---|---|
| Costume 0 .. N-1 | the hi-detail body once per costume, all at the same place (only Costume 0 shown: toggle the collections' visibility). All costumes share the same meshes (linked duplicates): edit the geometry once, every costume updates. Materials are per costume (linked to the object). |
| Low detail | lo-detail body (hidden) |
| Model parts | alternate pieces per joint and variant: hands (open/fist...), held props, effects. Objects `part<variant>_<hi\|lo>_j<joint>` (hidden) |
| Electric skeleton | the model drawn while electrocuted, `skel<id>_j<joint>` (hidden). It's drawn with the joint's body materials (MObjs) or its own inline textures (usually the shared skeleton texture) |
| Armature | the skeleton, bones `joint_00`.. (shared). Shown as sticks, in front, with names; bones reach toward their child joints, joints stacked on one spot (separate rotation axes) are short stubs, and bone collections Left (red, the fighter's left: +X) / Right (blue) / Center (yellow) group them. Display only: the import takes the joints from the game file |
| Hurtboxes / Collision / Stage objects | editing aids, never rendered: see [Hurtboxes](#hurtboxes-fighters) and [Collision and stage objects](#collision-and-stage-objects-stages) |
| Stage types / rooms | `Layer 0 - Background`, `Layer 1 - Stage (Z-buffered)`, and `Layer 2/3 - Foreground`; each contains `Room <joint>` collections for its DObj joints. Moving a room to another layer changes its stage type. |

Textures are external files (`textures/`, relative paths): keep the export folder
together. Judge colors in Material Preview: it runs the N64 setup (combiner, wrap
/ mirror / clamp per axis, per-material light colors); Solid view has one wrap mode
per texture and no combiner. What the preview does like the game:

- **Lighting**: lit materials are shaded by the game's light, not Blender's: shadow color +
  light color x max(0, N . L), shown as emission (Blender's lights don't change it). The
  direction is the scene's `n64_light_angle` (Scene > N64 Model > Light Angle): a stage's
  `light_angle` from its header, else the usual 20, 45. In battle the scene sets it once
  per frame for everything (decomp scvsbattle.c `scVSBattleFuncLights`): stage display
  lists set only their light colors, so stages and items are lit from the same direction.
- **Edges** by the render mode: translucent modes blend; alpha compare cuts at the blend
  color's alpha; anti-aliased texture edges (`CVG_X_ALPHA` + `ALPHA_CVG_SEL` + `AA_EN`,
  e.g. `00553048`) fade by coverage, alpha in eighths (the soft border of sprites; blended
  when the mode doesn't write depth, dithered when it does).
- **Texture format**: `n64_format` shows what the build stores: I / IA formats gray (I: alpha
  = intensity), each channel cut to its bits (I4 16 levels, RGBA16 5 bits...), 1-bit alpha
  for IA4, RGBA16 and CI palettes. CI colors aren't reduced in the preview (the build cuts
  them to 16 / 256).
- **Game Camera** (the scene camera; Numpad 0): the battle camera's 38 degree vertical field
  of view (gmcamera.c), 320 x 240, tilted down by the stage's `light_angle` z, framing a
  stage's camera bounds (else the model; a flat sprite facing x from its side). Stage
  renders composite the layers in the game's order (see Stages).

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

### Material settings (Blender: Material > N64 Model)

The **N64 Model** panel is the first thing in the Material tab, and all a material
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
(or Text Editor > `n64_panel.py` > Run Script). Or install
`n64_panel.py` once as an add-on (Edit > Preferences > Add-ons > Install from
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
python3 appender/scripts/ssb64_model/ssb64_model.py import OUT_DIR [TARGET] [--out BUILD_DIR] [--preview PREVIEW_DIR]
```
Blender reads `OUT_DIR/model.blend` into `OUT_DIR/import_work/` (`blend_to_gltf.py`), then
`import_fighter.py`, `import_object.py` or `import_stage.py` (by what was exported) writes the
files. TARGET defaults to the folder the export came from; `--out` writes the files to BUILD_DIR
instead.

### Fighters

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

(`import_object.py FOLDER NAME WORK_DIR [--out OUT_DIR]` on its own.)
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
- Material scripts (texture animations) are kept as they are unless edited in Blender
  ([Material animations](#material-animations)), and so is each display
  list's original end state (render mode restored...: items drawn with no render mode
  of their own, like stage hazards, inherit what a model leaves set).

### Stages

(`import_stage.py STAGE_DIR WORK_DIR [--out OUT_DIR]` on its own.)
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
  display list), +0x4 p_mobjsubs, +0x8 anim_joints (joint animations: editable), +0xC
  p_matanim_joints.
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
