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

# KIRBY COPY ABILITY (hat, held item, Kirby's actions)
What Kirby gets after inhaling the character. Everything optional:

Ryu/
- kirby_hat/kirby_hat.bin   the hat (the game file itself, added as is)
- kirby_hat/animations/...bin   Kirby's animations for the copied moves:
                                File.RYU_KIRBY_ANIM_FILENAME

kirby_hat.bin holds Kirby's body with the hat drawn after it (every copy-ability hat
replaces Kirby's whole body part), and optionally the item Kirby holds while the copied
special shows its "gun": the model part on his hand (joint 0x11) that Fox's / Wolf's
blaster uses (moveset event A0880000 shows it, A08FFFFF hides it). With a kirby_hat.bin,
Kirby copies the character (definitions.kirby_hat is implied) and wears that hat; without
one, definitions.kirby_hat: TRUE still gives him the parent's hat and power.
A character can wear another one's hat instead of having its own kirby_hat.bin: config.yaml
`kirby_hat_from: <character folder>` (e.g. Knuckles' variants: `kirby_hat_from: Knuckles`). The
ROM gets one hat file, listed in every sharer's main.bin req list (loaded once in a match).

Making / editing it (appender/scripts/ssb64_model):
- `kirby_hat.py new extra_characters/<Name>/kirby_hat`: a kirby_hat.bin with no hat yet
- `ssb64_model.py export extra_characters/<Name> OUT_DIR` also writes
  OUT_DIR/kirby_hat/model.blend: Kirby from the ROM (high-detail body, bones, all his
  animations, costume 0) wearing the hat; load more animation files with Action Editor > N64 Animation > Load Animation .bin. The hat is his body's model part `part<H>_hi_j02` /
  `part<H>_lo_j02`, the item his hand's `part<I>_hi_j13` / `_lo_j13` (Model parts collection,
  shown; H / I are in OUT_DIR/kirby_hat/manifest.json `kirby_hat`; no item yet: add a mesh
  `part<I>_hi_j13`, I = 1, skinned to joint_13). Held items point along joint 13's +X.
  Kirby's body triangles in the hat part are only for reference: the file always gets the
  ROM's Kirby body (materials, costume scripts) and the hat's own triangles after it.
- `ssb64_model.py import OUT_DIR` writes kirby_hat.bin back (with the character's files).
- `kirby_hat.py preview extra_characters/<Name>/kirby_hat/kirby_hat.bin OUT.png` (`--extra N`:
  extra hat N)
- extra hats: more variants of the hat (Kirby's body + other triangles), for something Kirby
  shows during a move on his body rather than his hand (Meta Knight's tornado). In the Kirby
  blend: more body parts `part<V>_hi_j02` / `_lo_j02` after the hat's (V > H, hidden; in order).
  Each gets a hat id, `KirbyHats.<NAME>_HAT_<n>` (the hat's: `KirbyHats.<NAME>_HAT`): a Kirby
  moveset shows it with `dw 0xA0300000 | KirbyHats.<NAME>_HAT_1` (his body's model part, joint 6);
  his next action puts the hat back.

Kirby's actions for the copied special go in the character's own code (the characters'
code is assembled after Kirby and J Kirby, so they come after Remix's Kirby actions), in
scopes of their own so their names don't mix with the character's actions. Their names are
their training mode strings:

scope Ryu {
    scope KirbyCopy {
        Character.add_new_action_params(KIRBY, Hadouken, -1, File.KIRBY_FALCO_NSP_G, Ryu.NSP_AIR, 0)
        Character.add_new_action(KIRBY, Hadouken, 0xE7, ActionParams.Hadouken, -1, RyuSpecial.NSP.main, ...)
    }
    scope JKirbyCopy { ...the same with JKIRBY... }
    KirbyCopy.action_map(RYU)
    dh 0xE4, Ryu.KirbyCopy.Action.Hadouken, Ryu.JKirbyCopy.Action.Hadouken
    KirbyCopy.action_map_end()
}

(-> Ryu.KirbyCopy.Action.Hadouken / Ryu.JKirbyCopy.Action.Hadouken; don't name the scope
Kirby: inside it, Kirby.* would mean the scope itself). The build counts these and grows
Kirby's / J Kirby's action arrays to fit. Point Kirby's NSP at the routines with
Character.table_patch_start(kirby_ground_nsp / kirby_air_nsp, Character.id.RYU, 0x4).

The Kirby actions run the character's own routines, which then see Kirby's player struct.
src/KirbyCopy.asm (smashremix_overwrite) makes them work for both:
- the action map (above): the character's actions and Kirby's / J Kirby's copies of them
- `jal KirbyCopy.change_action_` instead of `jal 0x800E6F24`: takes the character's action
- `KirbyCopy.load_action(dst, player)` instead of `lw dst, 0x0024(player)`: the character's action
- `KirbyCopy.load_char_id(dst, player)`: Kirby's copied power's id (e.g. for `== Character.id.KEN`)
- `KirbyCopy.var_address(dst, player, 0x0ADC, 0x0AF8)`: Kirby's own player variables at
  0x0ADC.. are his (0x0ADC: his copied power): a character variable there needs another
  offset for Kirby
- `KirbyCopy.branch_if_kirby(player, tmp, label)`
- `KirbyCopy.ground_collision_` / `air_collision_` (ground version <-> air version: the next /
  previous action), `ground_nsp_initial_` / `air_nsp_initial_` (vanilla 0xE4 / 0xE5 specials)
The macros are several instructions: never in a delay slot.

A copy built on one of Kirby's own copy abilities (a DK clone: Kirby's DK punch, from the parent's
kirby_ground_nsp / kirby_air_nsp): his own code for it sets his own statuses, so instead of new
actions to go to, give those statuses another action entry (routines, animation, moveset) while
he has the character's power, keeping the status ids:

    KirbyCopy.status_overrides(DKULT)
    dh Action.KIRBY.Punch, DKUlt.KirbyCopy.Action.GiantPunch, DKUlt.JKirbyCopy.Action.GiantPunch
    KirbyCopy.status_overrides_end()

(Kirby actions copied from his own with -1 for what stays the same: Ultimate DK.)

Kirby's moveset files: kirby_hat/moveset/NAME.bin, compiled like the character's (sounds,
sword trails) and inserted as kirby_NAME. Kirby has model parts on joints 6, 7, 12, 0x11,
0x12, 0x13 only (others ignore model part commands): a character moveset changing a part on
one of those, or a texture part (eyes / mouth), needs a Kirby copy without it.
Examples: Cloud, Ryu, Ken, Kazuya, Terry, Meta Knight (extra hat), Mega Man, Snake, Ice Climbers, Ultimate DK
(status overrides).
