# Extra items

An extra item is a folder `extra_items/<Name>/` (in your project, or in the appender's own
`extra_items/`). The appender adds it to Remix as a **standard item**: it gets an ON/OFF row on
a custom page of the Item Switch screen, a spawn rate on every stage, can drop out of crates/barrels/
capsules/eggs, and shows up in Training mode's item menu and held-item display.

Item ids continue Remix's own standard items (Stopwatch is 0x39, so the first extra item is
0x3A). Remix's stage and character items move up by one per extra item; they're all referenced
by name (`Item.KlapTrap.id`), so nothing else needs changing.

**Limit: 31 extra items.** The Item Switch screen shows the vanilla items on its first page,
then 15 custom items per page (Remix's 13 first, then the extra items), and R cycles through
the pages. The cap comes from the enabled bits: 12 free bits in the vanilla item bitmask plus
32 in Remix's extended one make 44 custom items, and Remix uses 13 of them.

## Folder

```
extra_items/ExtraItem/
    config.yaml
    ExtraItem.asm           <- same name as the folder
    extra_item.bin          <- model
    extra_item_hitbox.bin   <- ITAttributes (+ extra_item_hitbox_reqlist.txt: ${EXTRA_ITEM})
```

## config.yaml

```yaml
name: "Extra Item"          # Item Switch / Training mode name
rate: 5                     # spawn weight on every stage (Remix's items use 5)

files:                      # same as an extra import / stage files: (loaded every battle)
  - [extra_item, "0CBC", "3FFFC"]
  - [extra_item_hitbox, "3FFFC", "0000"]
sounds: {}                  # same as an extra import / stage sounds:

attributes:                 # ITAttributes values, written into the build copy of `file`
  file: extra_item_hitbox
  offset: 0x0
  weight: light             # light | heavy
  type: throw               # melee | shoot | throw | collision | other
  attack_count: 1
  damage: 4
  angle: 361
  knockback_scale: 100
  knockback_base: 10
  size: 160                 # hitbox diameter
  map_coll: { top: 40, center: 0, bottom: -25, width: 50 }
  # all fields: smashremix_extra/item/attributes.py
  # several footers (one per file): make attributes: a list of these blocks

name_images:                # optional, instead of the generated ones
  item_switch: row.png      # <= 112 x 12, white text on transparent
  training: menu.png        # <= 100 x 19 (IA: grey levels + alpha)
  training_hud: hud.png     # <= 100 x 16
```

The names are generated with the fonts the game used, one per place, laid out to match the
vanilla images (`smashremix_extra/item/name_images.py`, `hud_font.py`):

| image | font (`extra_resources/fonts/`) | style |
|---|---|---|
| Item Switch row | `myriad.ttf` (faux bold) | as the item lists |
| Training item menu | `myriad.ttf` + 1 px black shadow | mixed case, words joined (`ExtraItem`, like `MaximTomato`); two lines when wider than 86 px |
| Training held item | `quartz.ttf` (LCD, outlined) | upper case (`EXTRA ITEM`) |

Training menu names wider than 86 px take two lines, laid out as the vanilla
`MotionSensor / Bomb` sprite: 86x17, last word on line 2, each line the training menu
font squashed to 3/4 height (width kept), centered, 8 rows apart (a descender on line 2
makes it up to 2 rows taller). `training_name:` sets the menu text alone, and a `\n` in it
picks the split (or forces two lines):

```yaml
name: "Motion Sensor Bomb"
training_name: "Motion Sensor\nBomb"
```

Any character the font has works; one it lacks fails the build (give PNGs in `name_images:`
instead).

## The asm

`<Name>.asm` ends up inside `scope Item { scope <Name> { ... } }`, after a generated block
with `FILES.<FILE>.ptr` (the file's RAM address, filled at the start of every battle),
`FGM.<SOUND>` and `NAME.*`. It has to define what `Item.add_item` reads:

```
constant SPAWN_ITEM(spawn_)            // a0 parent GObj, a1 pos, a2 vel, a3 flags -> v0 item GObj
constant SHOW_GFX_WHEN_SPAWNED(OS.TRUE)
constant PICKUP_ITEM_MAIN(0)           // edible items (type 5) only
constant PICKUP_ITEM_INIT(hold_)       // a0 = item GObj, when picked up
constant DROP_ITEM(drop_)
constant THROW_ITEM(throw_)
constant PLAYER_COLLISION(0)
item_info_array:                       // ITDesc: id, &FILES.X.ptr, attributes offset, ...
constant ITEM_INFO_ARRAY_ORIGIN(origin())
```

Remix's own items in `src/items/` are working examples of this interface (states, map
collision, fighter checks); engine routine addresses come from ssb-decomp-re.

## What the appender changes

- `src/Item.asm`: `NUM_STANDARD_ITEMS`, `NUM_ITEMS`, `EXTENDED_ENABLED_BITMASK_DEFAULT`
  (extra items are ON by default), the item scopes and `add_item(<Name>)` after Stopwatch.
- `src/Stages.asm`: the item's `rate` in the per-stage custom item rate macro.
- `build/extra_items/ExtraItems.asm`: file loading, Item Switch name rows and Training mode
  sprite tables, used by hooks in `smashremix_overwrite/Item.asm`.
