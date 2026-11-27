"""
Extra items: `extra_items/<Name>/` folders (the project's, then the appender's own).

Each folder has:
- <Name>.asm: the item's code, implementing Remix's item interface (the constants
  Item.add_item(item) reads: SPAWN_ITEM, SHOW_GFX_WHEN_SPAWNED, PICKUP_ITEM_MAIN,
  PICKUP_ITEM_INIT, DROP_ITEM, THROW_ITEM, PLAYER_COLLISION, item_info_array,
  ITEM_INFO_ARRAY_ORIGIN).
- optional <Sub>/<Sub>.asm sub-parts (e.g. one folder per thing the item summons),
  included after <Name>.asm in folder order, then optional end.asm, which can list
  them: PART_COUNT, and {PART_<n>} = the n-th part's folder name (e.g. a table of
  {PART_<n>}.spawn_). (bass `global variable`s changed inside a nested scope
  don't survive it, so parts can't reliably register themselves.)
- config.yaml:
    name: "Extra Item"           # Item Switch / Training mode name
    rate: 5                      # spawn weight on every stage
    files: / sounds: / textures: # same as an extra import's (loaded every battle)
    attributes: {file: <files name>, offset: 0x0, damage: 4, ...} (or a list of these)
                                 # ITAttributes values written into the build copy
    training_name: "Motion Sensor\nBomb"   # optional: Training menu name, \n = line break
                                 # (default: `name`, two lines when too wide)
    name_images: {item_switch: a.png, training: b.png, training_hud: c.png}

The items become STANDARD items (item switch page 2, spawn rates, crates, training
mode), numbered right after Remix's own (0x3A, ...). Their code lands in
`scope Item { scope <Name> { generated FILES/FGM/NAMES ; <Name>.asm ; <Sub>/<Sub>.asm... ;
end.asm } }`.

Item Switch: page 0 = vanilla items, then 15 custom items per page (Remix's
first, then the extra items, adding pages as needed). The custom items' enabled
bits run out at MAX_CUSTOM_ITEMS custom items in total.
"""
import os

import yaml

from smashremix_extra.extra_import.processor import ExtraImportProcessor
from smashremix_extra.item import attributes as itattr
from smashremix_extra.item import name_images
from smashremix_extra.logger import logger

BUILTIN_ITEMS_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "extra_items"))
OUTPUT_ROOT = "build/extra_items"
EXTRA_ITEMS_ASM = f"{OUTPUT_ROOT}/ExtraItems.asm"

ITEM_SWITCH_ROWS = 15
# custom items: 12 in the vanilla item bitmask + 32 in Remix's extended one
MAX_CUSTOM_ITEMS = 12 + 32
# Remix's item switch list image (file 8 at 0xBD70) position, and its row pitch;
# row k (1 = first item) of the 12-row glyph images starts at 4 + 10k in the image.
LIST_X, LIST_Y = 125.0, 48.0


def _f32_bits(v):
    import struct
    return struct.unpack(">I", struct.pack(">f", v))[0]


class ItemProcessor(ExtraImportProcessor):
    def __init__(self, remix_standard_items):
        super().__init__()
        self.remix_standard_items = remix_standard_items
        self.items = []                     # [{name, folder, display, rate}]

    @staticmethod
    def list_items():
        """{Name: folder} of extra items (<folder>/<Name>/<Name>.asm), project first."""
        dirs = [os.path.relpath("./extra_items")]
        builtin = os.path.relpath(BUILTIN_ITEMS_DIR)
        if os.path.normcase(os.path.abspath(builtin)) != os.path.normcase(os.path.abspath(dirs[0])):
            dirs.append(builtin)
        found = {}
        for d in dirs:
            if not os.path.isdir(d):
                continue
            for name in sorted(os.listdir(d)):
                if name.startswith("_") or name in found:
                    continue
                if os.path.isfile(f"{d}/{name}/{name}.asm") and os.path.isfile(f"{d}/{name}/config.yaml"):
                    found[name] = d
        return found

    def process_all(self):
        for name, folder in self.list_items().items():
            self.process_item(name, folder)
        free = MAX_CUSTOM_ITEMS - self.remix_standard_items
        if len(self.items) > free:
            raise ValueError(f"{len(self.items)} extra items, but the item enable bits only have "
                             f"room for {free} more ({', '.join(i['name'] for i in self.items)})")
        self._write_asm()

    def process_item(self, name, folder):
        config = yaml.safe_load(open(f"{folder}/{name}/config.yaml", encoding="utf-8")) or {}
        self.process(name, folder, output_root=OUTPUT_ROOT, file_prefix="extra_item", wrap_scope=False)
        output_path = f"{OUTPUT_ROOT}/{name}"

        blocks = config.get("attributes") or []
        for attrs in ([blocks] if isinstance(blocks, dict) else blocks):
            fname = attrs.get("file")
            if not fname:
                raise ValueError(f"{name}: attributes: needs file: (one of its files:)")
            path = f"{output_path}/{fname}.bin"
            data = bytearray(open(path, "rb").read())
            itattr.apply(data, int(str(attrs.get("offset", 0)), 0), attrs)
            with open(path, "wb") as f:
                f.write(data)

        display = str(config.get("name", name))
        images = config.get("name_images", {}) or {}
        src = lambda key: f"{output_path}/{images[key]}" if images.get(key) else None
        index = len(self.items)
        custom = self.remix_standard_items + index      # 0-based among custom items
        sprites = [
            name_images.sprite_asm("item_switch", name_images.item_switch_image(display, src("item_switch")), "i4"),
            name_images.sprite_asm("training", name_images.training_image(str(config.get("training_name", display)), src("training")), "ia8"),
            name_images.sprite_asm("training_hud", name_images.training_hud_image(display, src("training_hud")), "ia8",
                                  name_images.TRAINING_HUD_TINT),
        ]
        with open(f"{output_path}/generated.asm", "a", encoding="utf-8") as f:
            f.write("\n// Name sprites\nscope NAME {\n" + "\n".join(sprites) + "\n}\n")

        self.items.append({"name": name, "folder": folder, "display": display,
                           "rate": int(config.get("rate", 5)),
                           "page": 1 + custom // ITEM_SWITCH_ROWS, "row": 1 + custom % ITEM_SWITCH_ROWS})
        logger.info(f"Extra item {name} ({display}): Item Switch page {1 + custom // ITEM_SWITCH_ROWS} "
                    f"row {1 + custom % ITEM_SWITCH_ROWS}")

    def _write_asm(self):
        os.makedirs(OUTPUT_ROOT, exist_ok=True)
        n = len(self.items)
        loads, draws, objects = [], [], []
        for i, item in enumerate(self.items):
            loads += [f"        jal     Item.{item['name']}.load_files_", "        nop"]
            uly = LIST_Y + 4 + 10 * item["row"]
            draws += [
                f"        // {item['display']}",
                "        lli     a0, 3",
                "        lli     a1, 12",
                f"        li      a2, Item.{item['name']}.NAME.item_switch",
                "        li      a3, Render.NOOP",
                f"        li      s1, 0x{_f32_bits(LIST_X):08X}",
                f"        li      s2, 0x{_f32_bits(uly):08X}",
                "        li      s3, 0xFFFFFFFF",
                "        or      s4, r0, r0",
                "        lui     s5, 0x3F80",
                "        jal     Render.draw_texture_",
                "        nop",
                "        li      at, item_switch_objects",
                f"        sw      v0, 0x{4 * i:X}(at)",
                "        lli     at, 1",
                "        sw      at, 0x007C(v0)              // hidden until its page",
            ]
        menu = [f"    dw Item.{it['name']}.NAME.training" for it in self.items] or ["    dw 0"]
        hud = [f"    dw Item.{it['name']}.NAME.training_hud" for it in self.items] or ["    dw 0"]
        content = "\n".join([
            "// Auto-generated by the appender (extra_items/) - do not edit",
            "scope ExtraItems {",
            f"    constant COUNT({n})",
            "",
            "    // @ Description",
            "    // Loads every extra item's files. Called at the start of every battle.",
            "    scope load_files_: {",
            "        OS.routine_begin(0x20)",
            *loads,
            "        OS.routine_end(0x20)",
            "    }",
            "",
            "    // Item Switch name rows (page 2), under Remix's list",
            "    OS.align(4)",
            "    item_switch_objects:",
            f"    fill 0x{4 * max(n, 1):X}",
            "",
            "    // @ Description",
            "    // Draws the name rows (hidden). Called from Item.item_switch_setup_.",
            "    scope item_switch_setup_: {",
            "        addiu   sp, sp, -0x0030",
            "        sw      ra, 0x0004(sp)",
            "        sw      s1, 0x0008(sp)",
            "        sw      s2, 0x000C(sp)",
            "        sw      s3, 0x0010(sp)",
            "        sw      s4, 0x0014(sp)",
            "        sw      s5, 0x0018(sp)",
            *draws,
            "        lw      ra, 0x0004(sp)",
            "        lw      s1, 0x0008(sp)",
            "        lw      s2, 0x000C(sp)",
            "        lw      s3, 0x0010(sp)",
            "        lw      s4, 0x0014(sp)",
            "        lw      s5, 0x0018(sp)",
            "        jr      ra",
            "        addiu   sp, sp, 0x0030",
            "    }",
            "",
            "    // @ Description",
            "    // Shows the name rows on Item Switch page a0 (1 = first custom page), hides",
            "    // the rest (a0 = 0: all hidden). Only uses t8 / t9 / at.",
            "    scope item_switch_display_: {",
            *([f"        li      t9, item_switch_objects",
               *[x for i, it in enumerate(self.items) for x in (
                   f"        lw      at, 0x{4 * i:X}(t9)",
                   f"        beqz    at, _skip_{i}",
                   f"        xori    t8, a0, {it['page']}           // t8 = 0 on its page",
                   "        sltu    t8, r0, t8                  // 0 = show, 1 = hide",
                   "        sw      t8, 0x007C(at)",
                   f"        _skip_{i}:")]] if n else []),
            "        jr      ra",
            "        nop",
            "    }",
            "",
            "    // Training mode Sprite* tables (index = item - Remix's standard items)",
            "    training_menu_sprites:",
            *menu,
            "    training_hud_sprites:",
            *hud,
            "}",
            ""])
        with open(EXTRA_ITEMS_ASM, "w", encoding="utf-8") as f:
            f.write(content)

    # ---- src patches -----------------------------------------------------

    def item_includes(self, src_dir="src"):
        """Lines for src/Item.asm, inside `scope Item`, before the add_item list."""
        lines = []
        rel = lambda path: os.path.relpath(path, src_dir).replace("\\", "/")
        for it in self.items:
            base = f"{it['folder']}/{it['name']}"
            lines += [f"    scope {it['name']} {{",
                      f'        include "{rel(f"{OUTPUT_ROOT}/{it["name"]}/generated.asm")}"',
                      f'        include "{rel(f"{base}/{it["name"]}.asm")}"']
            # sub-parts in their own folders (<Sub>/<Sub>.asm, e.g. one per assist of an
            # item that summons things), then end.asm (e.g. tables the parts registered into)
            parts = [sub for sub in sorted(os.listdir(base)) if os.path.isfile(f"{base}/{sub}/{sub}.asm")]
            for sub in parts:
                lines.append(f'        include "{rel(f"{base}/{sub}/{sub}.asm")}"')
            if parts:       # for end.asm: {PART_<n>} = each part's scope name
                lines += [f"        define PART_{n}({sub})" for n, sub in enumerate(parts)]
                lines.append(f"        constant PART_COUNT({len(parts)})")
            if os.path.isfile(f"{base}/end.asm"):
                lines.append(f'        include "{rel(f"{base}/end.asm")}"')
            lines.append("    }")
        return lines
