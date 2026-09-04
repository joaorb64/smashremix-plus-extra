# Extra stages guide

## Stage folder layout

```
MyStage/
  config.yaml
  header.bin
  stage.bin
  background.bin
  header_reqlist.txt   -> replace lines referencing the original STAGE / BACKGROUND
                          files with ${STAGE} / ${BACKGROUND}
  icon.bmp             -> optional, auto-generated from the name if missing
  series_logo.png      -> optional SSS series logo
  hazards.asm          -> optional, see below
  <name>.bin           -> optional imported bin files (see "files:")
  sounds/<name>.aifc   -> optional imported sound effects (see "sounds:")
```

## config.yaml

```yaml
offsets:
  header:     ["0098", "0014"]
  stage:      ["52E4", "3FFFC"]
  background: ["269D0", "3FFFC"]

name: "My Stage"
series: MARIO                 # series logo constant, or the folder name
                             # if series_logo.png is present
hazard_type: "HAZARDS"       # NONE | HAZARDS | MOVEMENT | BOTH
mushroom_kingdom_camera: true

music:
  main: "..."
  occasional: "..."
  rare: -1
  rare2: -1

spawn_locations:
  default: { p1: [-2500, 0], p2: [2500, 0], p3: [1000, 0], p4: [-1000, 0] }
  neutral: { p1: [-2500, 0], p2: [2500, 0], p3: [1000, 0], p4: [-1000, 0] }

# --- MPGroundData (header.bin) - any subset; patched in place ---
blast_zones:   { top: 5700, bottom: -1500, left: -6000, right: 6000 }
camera_bounds: { top: 3900, bottom: -1500, left: -4200, right: 4200 }
light_angle:   [80, 25]                 # [pitch deg, yaw deg]
magnifying_glass_color: "#88CCFF"       # #RRGGBB or [r,g,b] - tints the far-player
                                        # zoom lens + death colour-anim.
                                        # Overwritten in Training Mode.

# --- rebirth (revival) platform - the kind-0x20 map object in stage.bin ---
rebirth: [0, 2800]                      # [x, y]; the stage must already
                                        # have one (add in GE)

# --- map collision - see the "collision:" section below ---
```

## hazards.asm

If a `hazards.asm` file is present, it is compiled into

```
scope <FOLDER>_HAZARDS {
    scope FILES { scope <NAME> { id / ptr / offsets... } ... }  // from files:
    scope FGM   { ... }   // auto-generated, from sounds: (see below)
    // hazards.asm file content
}
```

and `Hazards.<FOLDER>_HAZARDS.setup` is registered as the stage's setup
function (replacing the default clone function). It must define a
`scope setup:` that returns via `jr ra`. See `extra_stages/BattleHarbor` for
a minimal example.

## files:  (imported bin files, like a character's `files:`)

```yaml
files:
  - [extra_model, "022C", "3FFFC", { footer: extra_model_hitbox }]
  - [extra_model_hitbox, "3FFFC", "0040"]     # the ITAttributes footer .bin
  - [misc]                                     # offsets default to "3FFFC"
```

`[name, tableOffset, resourceOffset, {opts}]`. Each `<name>.bin` in the stage
folder is appended as a game file; an optional `<name>_reqlist.txt` beside it is
honored. Each import produces, inside `scope FILES.<NAME>`:

```
FILES.<NAME>.id    ->  the new file id
FILES.<NAME>.ptr   ->  a word; setup fills it with the RAM addr via
                       resolve_stage_file(FILES.<NAME>.id, FILES.<NAME>.ptr)
```

For a GoldEditor self-relocating model .bin the appender also emits the byte
offsets it finds inside the file, so ASM never hard-codes them (a GE re-export
just regenerates the numbers):

```
FILES.<NAME>.head                 fixup-chain head
FILES.<NAME>.obj0, obj1, ...       each DObjDesc list
FILES.<NAME>.tex0, tlut0, ...      each image / palette the DLs reference
FILES.<NAME>.mobjsub               the MObjSub  (with { footer: ... })
FILES.<NAME>.sprites               its 3-entry sprite pointer table
FILES.<NAME>.sprite0, sprite1, ..  the sprite image data (the skin variants)
```

`{ footer: <name> }` names the `<name>_hitbox.bin` whose ITAttributes footer
points into this model - needed for the mobjsub / sprites / sprite* offsets.

Load a file at runtime with `Render.load_file_` (a0 = FILES.<NAME>.id, a1 = the
`.ptr` slot); reference it from `header_reqlist.txt` with `${<NAME>}` to load it
with the stage.

## sounds:  (imported sound effects / FGM, like a character's `sounds:`)

```yaml
sounds:
  custom_sfx: {}
  custom_sfx2: { sample_rate: 32000, fgm_type: VOICE, reverb: 0, length: -1 }
```

Each key maps to `sounds/<key>.aifc` in the stage folder - or to
`sounds/<key>.wav` (16-bit PCM, mono or stereo), which is converted to the
game's format automatically at build time and resampled to `sample_rate`. If an
up-to-date `.aifc` is already there beside the `.wav`, it is used as-is.
Defaults: sample_rate 16000, fgm_type VOICE, reverb 0, length -1 (auto). Each
produces, inside `scope FGM`:

```
FGM.CUSTOM_SFX   ->  the new FGM id
```

Play it with `li a0, FGM.CUSTOM_SFX` then `jal FGM.play_` (register-safe) - set
a0 BEFORE the jal, `li` is a two-instruction load and would split across the
delay slot. Stage sound ids continue the sequence after all character sounds.

## collision:  (rewrite stage.bin's map-collision from YAML)

A flat list, one entry per collision line. The appender rebuilds the 4
`MPGeometryData` arrays, appends them to stage.bin and repoints the (self-
relocating) pointers; header.bin is not touched. `stage_collision.py STAGE.bin
--emit-yaml` prints this list from an existing stage so you can start from what
GoldEditor made.

```yaml
collision:
  - {group: 1, floor: [-2100,1807, 2100,1807], flags: [drop_through]}
  - {group: 2, floor: [-7500,1807, -2100,1807]}
  - {group: 2, rwall: [-2100,-120, -2100,1807], flags: [ledge]}
  - {group: 6, floor: [2100,1807, 2700,1807, 7500,1807]}   # polyline: just add points
```

Each entry: `group:` (yakumono id, toggled from hazards.asm) + exactly one of
`floor | ceil | lwall | rwall`, whose value is a flat x,y,x,y,... list - two
points for a straight line, more for a polyline. `flags:` is optional -
`drop_through`, `ledge`, and/or a surface-index int.

Points at the same (x,y) and flags auto-weld to one vertex so lines can share an
index where you want them to connect. Disconnected groups and lone endpoints are
fine on a stage.

A stage supports at most 6 collision groups (GoldEditor caps there and so does
the engine's stage-object loader); using a 7th is a build error.

### Inspecting

```
python3 scripts/stage_collision.py STAGE.bin              # decode + dump
python3 scripts/stage_collision.py STAGE.bin --emit-yaml  # -> collision: list
python3 scripts/stage_collision.py STAGE.bin --draw layout.png --header header.bin
python3 scripts/stage_collision.py STAGE.bin --rebirth 0,2800  # move revival platform
```

`--draw` renders a top-down diagram: collision lines coloured by group (each
labelled `L<line id> g<group>`), every point labelled with its `x, y`, a ring
around any grab-able `ledge` point, plus blast zones, camera bounds, and map
objects (player starts, item spawns). The library lives in
`smashremix_extra/stage/` (`collision.py` / `ground.py` / `draw.py`); the script
is a thin CLI over it. Every stage build also drops `collision_layout.png` + a
per-group `collision_layout_groupN.png` next to the built stage files.
