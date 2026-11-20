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

Status: export and import work. Unedited round trips of Meta Knight and
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
A new character starts from a vanilla fighter: `ssb64_model.py new CAPTAIN extra_characters/<Name>
[--name "Display Name"]` (`new_character.py`) writes its main.bin / character.bin (the ROM's),
reqlists, config.yaml (offsets, base_character, costumes counted from the costume scripts, files
6..9) and an empty main.asm scope; every action plays the base's animations and moves until the
import gives it its own. Then export that folder.

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
`previews/stage.png`: the whole stage from the six sides; `previews/camera.png`: the whole stage
through the game camera (the .blend's `Game Camera`: 38 degree perspective framing the camera
bounds, tilted by `light_angle` z); `previews/layers.png`: each
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

#### Editing joints and layers: the N64 Stage panel

3D View sidebar > **N64** > **N64 Stage** (stage .blend files) shows each layer as a tree, in draw order
(top to bottom), each joint indented under its parent. Each row: a mesh icon (selects the joint's meshes;
a dot: it has none), a bone icon (selects its bone, in Pose Mode: to animate it), its name (**Selected
Joint > pencil** names it; else its meshes: faces and material) and small icons: animated, new, collision
group, locked. Layers fold. Click the name: **Selected Joint** below shows its parent, children, what it's
painted after / before, its meshes (**Put Selected Meshes Here**), animation, collision group, and actions.

- **+** on a layer: **Add Joint** under any parent (no rest transform: it's placed by its animation).
- Selected Joint: **Up / Down** move it (with its children) before / after its sibling, **Parent** hangs
  it from another joint (it keeps its place in the world), **Remove** (its children move up; its meshes
  must go first). Animations read from the game stay as the game's scripts while unchanged; edited, linear
  keys store as two per segment (**Make Linear**).
- **Remove Layer** (layers 2 and 3): the whole layer goes; the game then skips it.
- **Put On Joint**: the selected meshes go on a joint (its layer collection, the joint's vertex group,
  the armature). Model them around the joint's origin.
- Animate a joint in Pose Mode like the others (Bone > N64 Joint Animation): location / rotation are
  relative to its parent joint, in game space.

The game stores a layer's joints depth first and draws them in that order (later = over earlier on the
painted layers 0, 2 and 3), so after every edit the panel renumbers the layer depth first: bones,
their vertex groups, animations and the meshes' joint follow. Each bone remembers which original joint
it is (`n64_joint_orig`); the import (`import_stage.apply_joint_map`, from blend_to_gltf's `joint_map`)
rebuilds the layers from that: a joint keeps its original's transform, DObjDesc flags, animation script
and MObj order; new ones have no transform. An unedited layer stays byte-identical. A removed layer's
header fields are emptied (their external pointers and `header_reqlist.txt` lines go).

Collision groups and joints: collision group N moves / hides with layer 1's joint N (a hazard moves a
platform's collision by animating that joint, and toggles a group by its number). The panel shows each
layer 1 joint's group (and its edge count, or "empty": a group the stage's count reserves without lines);
selecting a collision group mesh shows its joint (N64 Collision panel), with a button to select it. The
count is the stage's own (scene `n64_collision_groups`, from the export), the same the import checks.

Locked: joint 0 (the layer's root), layer 1's joints 0..N (they carry the collision groups, by index),
layers 0 and 1 themselves. Hazard code may also address joints by index: check it after renumbering.
On a layer drawn the "secondary" way (MPGroundData `layer_mask` bit set) every joint's display list is
written as a DObjDLLink list, as the game reads them there.

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

### Fighter animations

Every animation the fighter plays, one Blender action each (kept, not assigned: pick one in the
Animation workspace's Action Editor; the scene's frame range follows the action picked).

Each is named after its file and every action that plays it, ` | ` between (`attackDash |
DASHATTACK | DashAttack`): searching the Action Editor's list for any of them finds it.

- **Character folder**: `animations/*.bin`. The flags each one is read with
  come from `main.asm` (`edit_action_parameters`, `add_new_action_params`,
  `edit_menu_action_parameters`); `-1` takes the base character's, from
  `smashremix/roms/original.z64` like Remix's Character.asm macros do. Actions `main.asm` doesn't
  give an animation play the base character's: those are exported too, from the original ROM,
  as `<action> | ... (<PARENT>)` (on this character's skeleton, like the game plays them).
- **ROM** (vanilla fighters): the fighter's motion table, one action per animation file, named
  after the actions playing it (Remix's `Action.asm` names; `Menu <n>` for the menu poses).

Why the flags matter (decomp `ftMainSetStatus`): an animation file is one script pointer per
joint, in the order the game walks the fighter's joint tree from TopN, and which joints that tree
has is the motion's flags: bit 31 - *i* turns hidden part *i* on (`attributes + 0x2D0`: XRotN,
TransN, YRotN, extra joints like the one Snake's taunt uses), added to the joints `setup_parts`
makes. The same file read with other flags animates the wrong joints. When several actions play
one file with different flags, the export uses those whose joints the file covers: as many
entries as joints, or more (the game stops at its last joint: Cloud's files carry one spare
entry). When more than one fits, an exact count wins unless another reads translations much
closer to the joints' rest (e.g. Cloud's idle: also Link's Menu 8, flags `0x80000000`, which
would shift every joint by one). A file no action plays gets flags guessed the same way (the
manifest's `flags_guessed`).

The **N64 Animation** panel (Properties > Object with the armature selected, and the Action
Editor's sidebar, open on the Action tab with this panel first) shows the action picked: which game actions play it, and its flags,
as the final hex value (editable) and as checkboxes: one per hidden part, named after the joint
it adds (this fighter's table: e.g. XRotN, TransN, YRotN, an extra joint), and the modes
(submotion script, 32-bit scripts, no translate scales, shield-pose file, animation locks). Each
box changes the hex value and the other way around. They're stored on the action
(`n64_flags`); the import doesn't write animations yet.

- **TransN**, when on, is the root motion: the game moves the fighter by its translation (a dash
  attack's 1700 units forward), not the body; here it moves the armature's root, so it shows.
- The scripts are the game's 16-bit format (`figatree.py`: values / 512 radians, / 4 units, / 4096
  scale; flag `0x8`: the 32-bit stage format).
- Keys: as few as follow the game's motion within ~0.5 degrees / 0.25 units, on the game's own
  key frames where they can be (`keyreduce.py`): vanilla animations keep a subset of theirs,
  baked ones (Blender's FBX export keys every frame) drop most of theirs.
- Not handled yet: translate scales (attributes `translate_scales`, flag `0x4`), animation locks
  (`0x1`), the guard animations stored in the shield-pose file (`0x2`); the import doesn't write
  animations yet.

### Shield pose (fighters)

How the body leans while shielding, per stick direction (decomp `ftcommonguard1.c`; attributes
+ 0x2D8 `dobj_lookup`, + 0x2DC `shield_anim_joints[8]`, both external pointers into the shield-pose
file). Exported from the character folder's `shield_pose.bin` (config.yaml `shield_pose: [internal
head, external head]`), or from the ROM (the file main.bin's 0x2D8 pointer is in). A folder whose
`shield_pose` is a ROM file ID, or that has none (its base character's), gets that file from the
original ROM (`smashremix/roms/original.z64`). The import writes it back (see Import).

The armature gets the game's hidden root joints above the body: `TransN` -> `XRotN` -> `joint_00`
..., and `YRotN` (XRotN's last child: where the shield sits). Their rest is the identity, so the
body doesn't move; no vertices go on them. Two actions, not assigned (the armature stays at
rest): the Animation workspace opens with its Dope Sheet in **Action Editor**, pick one there:

- **Shield**: frame = stick angle in degrees, the stick pushed all the way: 0 forward, 90 up, 180
  back, 270 down, 360 forward again. The game stores it as 8 sectors of 45 frames (sector *i* =
  frames 45 *i* .. 45 *i* + 45), each played at the angle within it.
  Keys sit exactly on the game's key frames (the sector starts, plus wherever a script sets a
  value, e.g. Mario's 44), with the game's values there. Between keys, the curves are Bezier
  handles fitted to the game's motion: the game interpolates each joint's Euler angles, while
  Blender turns the bone from its rest, so the two can differ slightly (the build prints the
  worst case: Mario 0.5 degrees, Snake 0.2).
- **Shield Neutral** (one frame): the pose each scripted joint goes back to as the stick returns
  to the center. The game blends every body joint that has a script, and YRotN: `neutral +
  (Shield - neutral) * stick range` (rotations as Euler angles). A joint without one keeps the
  guard animation's pose (shown here at its neutral value). XRotN's neutral is never used: it
  plays its sector script as it is (`ftCommonGuardInitJoints` starts at `dobj_lookup[1]`), so
  e.g. Snake's 90-degree XRotN neutral doesn't show in game.

The `Shield` collection (hidden at first; shown while the fighter's action is `Shield` or `Shield
Neutral`, hidden again after when it was shown that way) has **Shield Bubble**, a sphere on `YRotN` with the
fighter's `shield_size` (attributes + 0x74) as its radius: the shield at full health (the game's
30-unit sphere on YRotN, scaled `shield_size / 30`). config.yaml `attributes.shield_size`, when
there, is the size (the build writes it over main.bin's).

With the ROM, **Shield Graphic** next to it is the shield as the game draws it (file 163, from
`reference.py` -> `reference/shield.png`): a quad of `+-30` units with the bubble texture, on
YRotN's position, always facing the camera (the game billboards it: in Blender the panel turns it
to the largest 3D view every 0.1 s, or to the scene camera in camera view, and moves it to the
bubble's front so it's drawn over the fighter). Its half width follows the bubble: size x
`(0.65 x health / 55 + 0.35)`. Shield Bubble custom properties (preview only, not imported):
`n64_shield_health` (0..55), `n64_shield_opacity` (default 0.35, to see the fighter) and `n64_shield_port`
(0 P1 red, 1 P2 green, 2 P3 blue, 3 P4 / CPU black, 4 the frame it's hit). Color = lerp(port color,
white, texel), alpha = texel alpha x 0xC0, as `efManagerShieldProcDisplay` sets it.

Which joints the file covers, in its order: the tree `lbCommonAddDObjAnimJointAll` walks from
XRotN while guarding. That's the body joints `setup_parts` (attributes + 0x29C) creates, plus the
hidden parts (attributes + 0x2D0) the guard animations' motion flags turn on (0xA0000000 in every
vanilla fighter: XRotN and YRotN). So it's XRotN, the body joints in order without the hidden
ones (e.g. the last joint, hung under joint_00 only by other animations), then YRotN. The
manifest's `shield` entry lists them, and which have a script per sector.

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

The glTF carries every animation sampled once per frame (what the game plays). The .blend
rebuilds the curves with keys only where they're needed (`keyreduce.py`): the game's own key
frames first (where its scripts set values), more only where Blender's curve can't follow the
game's within tolerance (rotation ~0.002 degrees, 0.005 units), with Bezier handles fitted
between. Keys go per channel group: a bone's rotation curves share the union of the game's
rotation x / y / z keys (a quaternion turns all three axes at once), its location and its scale
curves their own; a group the game never animates keeps a single key. The import samples every frame, so an unedited animation comes back the same.

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
the material's action (60 frames per second, like the game), shown in Material Preview while
the timeline plays. The **N64 Model** panel's **Material Animation** box lists
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

One file per thing a material has (`tex_layout` 2):

| File | What | Edit it to |
|---|---|---|
| `<mat>.png` | the material's image with its palette as costume 0 draws it (always there) | paint costume 0 / the base drawing: a recolor changes costume 0's palette, a repaint the image |
| `<mat>_pal<p>.png` | palette p (p >= 1), shown on that image: what a costume using it looks like | recolor: each pixel's color becomes that palette slot's color |
| `<mat>_img<i>.png` | image i (i >= 1: facial expressions, alternate textures), drawn with costume 0's palette | redraw it. CI images keep their palette's colors (new colors map to the nearest one) |
| `preview/<mat>_c<N>.png` | only for a costume using another image AND another palette: its picture | paint it like any costume |

A costume shows the file of what it uses (costume 3 = `<mat>_pal3.png` for most fighters), so
painting a costume in Blender edits that file; the import takes each costume's image / palette
from the file it shows. More `<mat>_pal<n>.png` / `<mat>_img<n>.png` files numbered past the list
are added to it (made in any editor; **Reload Textures** shows them). Older exports
(`<mat>_c<N>.png` copies besides `_img` / `_pal`) import as before.

A .blend runs its own copy of this panel; when the tools' `n64_panel.py` is newer it updates
itself as the file opens. Older .blends: `ssb64_model.py update-panel OUT_DIR` (nothing else changes;
reopen the file).

**N64 Fighter > Detail: High / Low / Skeleton** shows the high detail model (the shown costume),
the low detail one (in that costume's colors) or the electric-shock skeleton; model part
variants follow the detail.

**N64 Fighter > Costume: Add (copy N)** makes the next costume, a copy of the shown one: its
collection and materials, and per material with a palette list a new `<mat>_pal<n>.png` to
recolor (a material without a palette list shares the source costume's texture; a non-CI one
gets a new `_img<n>`). What Blender doesn't show per costume comes from the costume it copied
(scene `n64_costume_sources`): MObj env / blend / light colors, and the colors of materials only
used by the low detail model or model parts; the prim color (a color-only material's color in
Blender) is the new costume material's, editable like any costume's. **Remove Last** removes the last costume and the files Add made for it. The
export takes the costume count from the file's costume scripts (config.yaml `num_costumes` only when
they don't say: it can count differently, e.g. the Ice Climbers' file holds Popo's and Nana's, twice
it). The import writes as many costumes as the .blend has (config.yaml `num_costumes` follows when
it matched the file's count, else it's left alone; it notes
`default_costumes` past the last costume and missing `stock_icon_<n>.png`). The Kirby hat .blend
keeps its own costumes.

**Reload Textures** (N64 Fighter) reloads every texture from disk (after editing outside Blender)
and counts, per material, its colors (most in one file, against its format: CI4 16, CI8 256),
images and palettes: listed under the button when there's more than one or too many colors, and in
the material's N64 Model panel.

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
raw command). The .blend opens in Material Preview using the scene's world (a plain gray) and
lights (none) instead of the studio HDRI: the materials light themselves (the game's light is in
the shader), and lighting the scene from the HDRI is what made Material Preview slow. Every material is drawn
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

### From Blender: Write Game Files

**3D View > N64 > N64 Game Files** > **Write Game Files** saves the .blend (and painted images),
then runs this import with `--out` into **Folder** (default `//to_project`, next to the .blend),
in the background: when it's done the panel lists the files to copy into the character / stage
folder (main.bin, character.bin, main_reqlist.txt, config.yaml when something in it changed,
animations, shield_pose.bin, kirby_hat/...), with **Open Folder** and the **Log**
(`import_log.txt`). The folder is emptied on each run (only one this button made).
**Write All (Every Animation)** is the same with `import --all`: also every animation with its own
file (`animations/*.bin`, re-encoded from Blender, edited or not; the base character's borrowed
ones only when edited, as each would become a new file and main.asm lines), the character's own
shield_pose.bin and config.yaml even when unchanged: a full set (a 67-animation character: ~30 s).

It runs with Blender's own Python (a sandboxed Blender, like the Flatpak, can't start the system's),
which needs Pillow and PyYAML: **Install Tools** (shown while they're missing) installs them with pip
into Blender's user scripts folder (`ssb64_model_libs`), once. With `--out`, config.yaml edits go to
a copy in the folder (kept only when it changed); without it, into the character's own.

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
- Display lists start with the base model's prologue (sync, light colors, combiner), up to the
  writer's own first command (`D9FFFBFF 00000000`): files imported before that rule carried one
  more copy of their first material's setup per import (4 commands per display list); the next
  import drops them, and round trips are byte-identical.

Sizes (unedited round trip): Meta Knight 75,296 -> 55,088 bytes (its hi and lo
models were duplicate data), Snake 180,748 -> 107,800.

#### Fighter animations

Only the animations edited since the export are written (blend_to_gltf.py compares each
action's keys, flags and Loop with what the export made). `import --all` writes every one that has its own file too. For each, every joint is sampled per
frame in the game's space and written in the 16-bit format (`figatree.py`): as few keys as play
it within ~0.23 degrees / 0.5 units, preferring the frames of the Blender keys, each segment
linear or cubic (whichever is smaller per joint), looping when Loop is on. Then:

- its own file: `animations/<file>.bin` is replaced; when its flags changed, every `main.asm`
  line playing it gets them (`0x8`, 32-bit scripts, is cleared: it's written in 16-bit);
- one of the base character's (`... (CAPTAIN)`): a new `animations/<action>.bin`, and `main.asm`
  gets an `edit_action_parameters` (`edit_menu_action_parameters`) line per action playing it.

`main.asm` keeps its line endings; with `--out`, it and the animations go there instead. Snake's
139 files re-encoded this way: 915 KB -> 771 KB (its motion is detailed: a key every 2-3 frames
stays needed at that tolerance).

#### Shield pose

When the `Shield` / `Shield Neutral` actions were edited (the build stores each bone's keys;
only the bones whose keys changed are read), the import writes a new `shield_pose.bin`
(`shieldpose.build_file`): the neutral DObjDesc array, the 8 sector arrays, the scripts. Each
edited bone's sectors are compared with the file's (rotations as matrices, ~3 degrees /
0.5 units); a changed one is sampled per frame in the game's space and written as an
AObjEvent32 script (`jointanim.build`, Euler angles continuous from the file's own), every other
script copied as it is. A joint the file gave no script in a sector gets one if it now moves
away from its neutral pose. Then main.bin's 0x2D8..0x2F8 pointers point into the new file, their
`main_reqlist.txt` lines become `${SHIELD_POSE}` (the build's ID for the character's own file:
needed when it used a ROM file), and config.yaml gets `shield_pose: [its internal head,
"3FFFC"]` (with `--out`: printed instead). The Shield Bubble's size (display size x scale) goes
to `shield_size` (attributes + 0x74) when it changed.

#### Costumes, expressions and model parts

**3D View > N64 > N64 Fighter** (fighters):

- **Costume**: shows that costume's collection (the others hidden) and gives the model parts and
  low detail its colors (object-linked `<material>_c<k>` copies: the body's, else one with the
  image's `_c<k>.png`).
- **Alternate textures** (texture parts: facial expressions, or any material whose image moveset
  events switch): per part, an image of its list (**Own**, shown only when a costume's script picks another image
  than 0: no override, the costume's own image; the game goes back to image 0 after an expression
  event resets), on every
  material the part switches (the joint's MObj at its index: body, low detail, model part
  variants; the part's joint / material index are Armature > N64 Fighter Joints', followed live).
  In costume 0 it's the list image itself (`textures/<mat>_img<i>.png`: paint it in the Image
  Editor, the import reads it); other costumes show it recolored to their palette (a preview).
  **Add**: a new image at the end of each of those materials' lists (a copy of the shown one,
  `textures/<mat>_img<n>.png`, as an `N64 Frame <n>` node: the import appends it; moveset event
  0x2B picks it by number); **Remove Last Added**: only images added in Blender. A part that
  points at a material without an image list is flagged, with the joint's materials that have one.
  **Bone > N64 Joint > Materials** lists the joint's materials in their MObj order (what a texture
  part's index counts) and which are alternate textures; **Make Alternate Texture** gives one a free
  slot (the game has 2; a fighter without the table gets one). A one-image material works: the
  first **Add** gives it a second image, and the import gives the material an image list.
  When config.yaml has `attributes: texture_forms` (the build writes them over main.bin's), those
  are the parts; the import writes edits back there when it can say them (a joint's n-th entry
  switches its material n), else removes the key and writes the files' container (a new 2-entry one
  in character.bin when main.bin's holds fewer entries: vanilla containers often hold one).
  Costumes should change palettes only: a costume script that picks the image too
  (Cloud's did) fights the alternate textures.
- **Model parts**: per joint, what it shows: **Body** (the body's piece), **None**, or a variant
  (variant 0 is what the game starts the joint with and goes back to when parts reset - it draws
  the body's piece at first, so a moveset event picking 0 changes nothing: variant 0 must look
  like the body. The first **Add (copy body)** on a joint makes variant 0 = the body and variant 1
  to edit; the import warns when variant 0 differs from the body. Each variant row shows its
  moveset event word, `dw 0xA0..`, with a copy button)
  (in the body piece's place, like the game's model part event / `ftParamSetModelPartID`).

**Bone > N64 Joint > Model parts** (the selected joint): the same picker, each variant's objects
(select icon: shows and selects them), **Add (copy body)** / **Add (copy <last>)**: the next
variant (`part<v>_hi_j<NN>` + `_lo_`, linked to Model parts, a copy of the body's piece or of
the last variant; with no low detail piece, the high one), and X on the last variant (only the
last: the others keep their ids, which moveset events and code use).

The import resets every preview first (blend_to_gltf.py: `n64_panel.reset_previews`), so what's
shown never changes what's written. A joint whose variant count changed (or that gets its first
variant) gets a new variant list in character.bin: the model part container's entry becomes an
external pointer, the old list's pointers leave main.bin's external chain and
`main_reqlist.txt` (so their data is freed), and when the moved entry was the start of
main.bin's internal pointer chain, config.yaml `offsets: main` gets the new start (with
`--out`: printed). Moveset events that pick a removed variant need changing by hand.

#### Joint attributes

The fighter attributes that name joints (`jointattrs.py`, decomp `FTAttributes`) are edited on
the bones, by bone name (the import turns them into joint ids):

- **Bone > N64 Joint** (any joint bone): what the game uses the joint for (root motion, item
  joints, foot IK, effect joints, expressions, accessory, hidden part, hurtboxes, model part
  variants, electric skeleton; a withheld joint nothing creates is flagged), and per joint:
  - **Created With the Fighter** (`setup_parts`, + 0x29C): off = withheld; it exists only while a
    hidden part adds it.
  - **Animation Lock** (`animlock`, + 0x2A0): in every motion without flag 0x1 (the guard
    animations too) the game freezes the joint as it was and ignores its animation
    (`ftParamSetAnimLocks`), so in those actions (and Shield / Shield Neutral) its bone is locked
    in Pose Mode (still shown). Flag 0x1 on: posable.
  - **Translate Scale** (`translate_scales`, + 0x324, NULL in every Remix fighter): its animated
    translation is scaled by it; any value other than 1 creates the table (in character.bin).
  - **Hidden part** (`hiddenparts`, + 0x2D0): **Make Hidden Part** turns the joint into the next
    one (motion flag bit 31 - index adds it; it stops being created with the fighter), with its
    parent (TopN: the top joint), placement among the parent's children and init flag. Only the
    last one can be removed (the parts after another would move to other flag bits). A table
    that grows moves to character.bin (main.bin's internal pointer becomes an external one;
    `main_reqlist.txt` gets a `${CHARACTER}` line).
- **Armature > N64 Fighter Joints**: light / heavy item joint (+ 0x33C / + 0x334), the slope foot
  IK thighs and shin points (+ 0x2FC..0x308), the 5 effect joints (+ 0x2A4: cycled through for
  electric / flame hits), expressions (texture parts, + 0x330: joint + material index per
  detail), the accessory part's joint (+ 0x32C, when the fighter has one), and the hidden parts
  list.

The N64 Animation panel's hidden-part checkboxes and the bone locks follow the edits right away.
Load / Save Animation .bin read the joint tree from the export's files: after changing what's
created or the hidden parts, import, then export again before saving animations with them.
The import writes only what changed (`joint attributes: unchanged` otherwise).

The special joints are colored in the viewport (pose bone colors over the side colors, bone
collection **Special Joints**): items green, slope foot IK thighs orange, alternate textures
cyan, effect joints magenta, accessory yellow, hidden parts gray (legend in Armature > N64
Fighter Joints). The colors follow the edits.

#### Removing joints

**Bone > N64 Joint > Remove Joint** removes the joint and every joint under it, with their meshes
(all costumes, low detail, model part variants, electric skeleton), vertex groups and hurtboxes.
It refuses while the joint is a hidden part, a hidden part's parent, a special joint (Armature >
N64 Fighter Joints: point it elsewhere first) or while another mesh has vertices weighted to it.
The game numbers joints in DObjDesc order, so the joints after it move down: the bones are
renamed to their new numbers (what moveset events use), each keeping its export number in
`n64_joint_orig` (Bone > N64 Joint shows it). The dialog lists the special joints after it and
asks: **Follow Their Bones** (the same bones, new numbers: what the moves expect) or **Keep
Their Numbers** (they land on the bones that take those numbers). The removed joints' animation
curves stay (renamed `n64_removed_joint_NN`), so unedited actions still match the export.
Deleting a bone by hand works too (no renumbering in Blender then).

On import (`blend_to_gltf.py` renames everything back to the export's numbers in memory and
reports the missing joints as `fighter_joints`; `jointmap.py` renumbers):

- character.bin: the DObjDesc arrays (hi, lo) and the per-joint material / costume script arrays
  lose the entries; main.bin: the model part container and electric skeleton tables (entries
  move up; the pointer chains and `main_reqlist.txt` are rebuilt in field order);
- joint attributes: setup / animlock bits compacted, effect / item / foot IK / expression /
  accessory / hidden part ids renumbered; hurtboxes renumbered (same order);
- every animation: the removed joints' entries go (the scripts byte for byte; edited actions are
  encoded, then the same): own files are rewritten, the base character's become the
  character's own files (`animations/`, main.asm `edit_action_parameters` /
  `edit_menu_action_parameters` lines with `-1` flags). Files whose entries end before the
  removed joint stay the parent's;
- the shield pose: the neutral array, the 8 sector arrays and guard animations inside it
  (shield-pose motions) lose the entries in place (offsets kept); written to `shield_pose.bin`
  (config.yaml `shield_pose`, `${SHIELD_POSE}` reqlist lines);
- moveset events (hitboxes, effects, hurtbox parts, model parts: 7-bit joint ids) aren't
  rewritten: the actions still run the base character's scripts (`-1` in main.asm), so the import
  lists the ones whose scripts name joints whose number changed (or are gone) - give those their
  own scripts in main.asm where it matters - and the same for the character's `moveset/*.bin`.
  ASM code naming joints by number isn't read: the renumbering is printed.

Only whole branches go (a joint whose children stay would need its transform pushed into them);
hidden part joints can't be removed. After the import, export again before more joint edits or
Load / Save Animation .bin.

#### Adding joints

**Bone > N64 Joint > Add Joint** adds a joint under the selected one, stored after that joint's
other descendants (joints are kept depth first), so the joints after it move up one (same
dialog for the special joints after it). It starts at its parent's head: move it in Edit Mode
(its rest position is what the import uses; its rotation is the parent's). **Put Selected Meshes
Here** makes the selected meshes its body piece: `hi_jNN` in Costume 0 with a copy in every
other costume, skinned to the joint only; a mesh in Low detail becomes `lo_jNN`, and a joint
without one gets a copy of the high piece. The import (temporary numbers after the export's,
`fighter_joints.order` / `added`) gives it a DObjDesc entry (depth, translation from the
parent), setup bit on, no animation in any file (NULL entries: it stays at rest on its parent),
a rest entry in the shield pose; the tables that grow (main.bin's model part container and
electric skeleton tables, the shield pose's arrays) are rebuilt at the end of their file and
re-pointed (config.yaml `offsets: main` follows when the chain head moves). New joints can have
model part variants (their list goes to their slot of the rebuilt container) and animations:
Blender samples them with their parent's axes at their own position (what the import gives them),
and an edited action is encoded straight in the new joint order. Existing joints keep their
parents (no reparenting yet).

An added joint can be a hidden part (**Make Hidden Part** on it: the next motion flag bit adds it;
turn that bit on in an action's flags in the N64 Animation panel): the import puts it in the hidden
part table (grown into character.bin), leaves its setup bit off, and every animation with that flag
gets its entry (an edited one its script; NULL: it stays at rest on its parent).

#### Moving joints to another parent

**Bone > N64 Joint > Set Parent** (or Alt+P > Keep Offset in Edit Mode) moves a joint and its branch
under another joint, where it is (its world rest stays). The branch goes after the new parent's
other children (depth first), the joints are renumbered. Its animation:

- **Follow the New Parent** (default; what Alt+P does): its keys stay as they are, relative to
  its rest, so it moves with the new parent (a gun moved to a hand goes with the hand). Import:
  each script becomes new rest x old rest^-1 x old local; not animated: it stays at its new rest.
- **Keep Its Motion**: its keys are re-expressed in every action (a key per frame) so it moves
  in the world as before; the import recomputes its script from both parents' poses. Where the
  new parent scales unevenly the exact motion would need a shear the game can't store: the
  closest pose is written and the import names those animations.

Either way only the moved joint's scripts are recomputed (in the file's own format, 16- or 32-bit);
every other joint's stays byte for byte, and actions not edited before stay unedited (their
signatures follow). Its DObjDesc entry gets the rest relative to the new parent and the moved
branch new depths; the shield pose's neutral entry and sector scripts follow the same way. The
root joint and hidden part joints stay where they are (a hidden part's parent is its Bone > N64
Joint setting); a joint can't go under its own branch or into a hidden part's branch.

Blender's own tools work too: deleting a joint bone (X) or adding a bone (E, Shift+A, then parent it
to a joint) in Edit Mode is renumbered like the buttons once the armature leaves Edit Mode (the
panel's update handler, `blend_joints.sync`; the result shows in Bone > N64 Joint): a deleted
joint's meshes, vertex groups and hurtboxes go, a new bone becomes the next joint of its parent's
branch, the others are renamed.

#### Slope foot IK (legs)

On slopes the game bends each leg with 2-bone IK (`legs.py`; decomp
`mpCommonUpdateFighterSlopeContour`). Attributes: + 0x2FC right thigh joint (fp->joints index),
+ 0x300 a length down the right shin, + 0x304 / + 0x308 the left's (the decomp calls the lengths
`joint_*foot_rotate`). The IK moves the point that far down the shin by the floor's height
difference under it (it doesn't put it on the floor), so the length only picks a point on the
shin: a base character's works. The ids must point at the legs (thigh -> hip at the same place
-> knee along x -> ankle): when they don't, the export and the import print a note with the
joints that look like the legs. Nothing is changed automatically.

The IK assumes the animation keeps the hip on the thigh joint and the knee at its rest offset
(it bends the knee around z only, and reads the knee's current x offset as the thigh length).
Animations that move those joints, or stand with straight knees, can make the legs jump on
slopes.

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
skeleton entries), accessory part models (their joint is editable: see Joint attributes).

## Kirby copy-ability hats

A fighter folder with `kirby_hat/kirby_hat.bin` (see `kirby_hat.py`) exports a second blend,
`OUT_DIR/kirby_hat/model.blend`: Kirby from the ROM (high-detail body, bones, all his animations,
costume 0) wearing
the hat as his body's model part (`part<H>_*_j02`) and holding the item as his hand's
(`part<I>_*_j13`), both shown in Model parts. `import OUT_DIR` writes the character's files and
then `kirby_hat.bin` from that blend (`import OUT_DIR/kirby_hat` does only the hat). The hat
part's Kirby-body triangles are reference only: the file always takes the ROM's body.
The character's own Kirby animations, `kirby_hat/animations/*.bin`, come first in that blend,
named `<file> | <Kirby actions> (copy)`, with the flags from main.asm's
`add_new_action_params(KIRBY / JKIRBY, ...)` lines. When you edit one, the import writes it back to
`kirby_hat/animations/` (if you change its flags, update the main.asm lines yourself). Edits to
Kirby's ROM animations are not written.
`kirby_hat.py new HAT_DIR` starts one, `kirby_hat.py preview BIN OUT.png` renders it.

## Reference items and opponent (fighters)

Fighter exports also write `OUT_DIR/reference/` (`reference.py`; `--no-reference` skips it) and
model.blend gets a hidden **Reference** collection, never imported back:
- **Items**: Ray Gun, Beam Sword, Hammer (light: on `attr->joint_itemlight_id`) and Crate,
  Barrel (heavy: `attr->joint_itemheavy_id`), from the ROM, one collection each. Like the game
  (itMainSetFighterHold), each sits in its hold joint's frame and follows the animation: a
  Child Of constraint on the bone with scale off (the game takes the joint's scale out), its
  offset from the rest pose. The game scales the fighter by `attr->size` but not what it holds,
  so next to the fighter (drawn at model size) items are 1 / size.
  Held items are drawn without their root joint's transform (the game ejects the root DObj when
  the item is made and, when held, sets the descs again from the new hold root down): the
  Crate / Barrel sit their child joint's offset (z +225 / +210) away from the hold joint.
- **Opponent**: Mario (armature `Opponent`) with his grabbed / thrown animations
  (`Opponent: CapturePulled`, `Thrown1`, `Thrown2`, `ThrownDK`...; pick one in its Action Editor).
  Like a grabbed fighter (ftCommonCapturePulledRotateScale) he's turned like the heavy item joint
  with his TransN joint on it (his animations' TransN moves are dropped: the game cancels them),
  at his own size relative to the thrower's (Mario's `attr->size` / the fighter's).
  Which thrown animation a throw plays comes from the thrower's `attr->thrown_status`.
Scripts that look for the fighter's armature skip ones flagged `n64_reference`.

**3D View > N64 > N64 Fighter > Reference**: one button per item (shows it in the hand, again
hides it; one at a time) with its hold joint (button: selects that bone in Pose Mode),
**Grabbed Opponent** (show / hide) with his animation (the `Opponent: ...` actions). When
Armature > N64 Fighter Joints names another light / heavy item joint, **Move to <bone>** puts the
reference there (the joint's rest frame without scale, from the export's model.gltf axes; the
opponent follows the heavy one).
Mario is minimal: high-detail model, flat colors (each texture's average), the thrown animations.

## Loading animation files in Blender

Action Editor sidebar > N64 Animation > **Load Animation .bin**: an animation file (the
character's `animations/`, `kirby_hat/animations/`, a ROM extract...) becomes a new action on the
fighter (`anim_load.py`), keyed like the exported ones (every bone; keys reduced within
keyreduce.TOL_ANIM). Its motion flags are guessed from the file's joint count unless given
(Flags 0x in the file dialog's options). The export saves the fighter's files for this
(`OUT_DIR/fighter/`, manifest `fighter_files`). A loaded action isn't one of the exported
animations: the import doesn't write it back.

**Save Animation .bin** (next to it) writes the current action to a file (`anim_save.py`):
sampled and encoded like the import writes edited animations (blend_gamespace.sample_action,
figatree.encode_animation), for the flags in the dialog (default: the action's, else 0) and its
Loop setting. Files are written in the 16-bit format, so the motion playing it needs the flags
without 0x8 (the report says which). Default path: the file the action was loaded from, else the
character's `animations/<action>.bin`.

**Rest Pose** (below them) puts the fighter back at rest: it picks the `Rest Pose` action (one key
per bone at frame 0, at rest; the export makes it, the button makes it when missing). It isn't a
game animation: the import ignores it.

**Lock Bones Not in This Animation** (N64 Animation panel, on by default): the bones the active
action's file has no script for are locked (transform locks) and hidden - the joints its motion
flags leave out of the tree (figatree.joint_order: hidden parts whose bit is off, e.g. TransN /
XRotN / YRotN in an animation without them), or outside XRotN's subtree for the Shield actions.
Ticking a hidden part's flag unlocks its bone. The tree comes from the manifest's `joint_tree`
(armature `n64_joint_tree`); .blends built before it read it once from the export's fighter files.
Only the bones locked this way are unlocked again (your own locks stay).
