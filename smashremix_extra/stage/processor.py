import os
import re
import shutil
import yaml

from smashremix_extra.constants import SMASHREMIX_PATH as smashremix_path
from smashremix_extra.image_appender import append_image, get_image_data, ImageMode
from smashremix_extra.gen_stage_icon import create_stage_icon
from smashremix_extra.file_manager import FileManager
from smashremix_extra.logger import logger
from smashremix_extra import hex_util


class StageProcessor:
    """Processes extra stage folders and accumulates patch data."""

    def __init__(self):
        self.stage_headers_strings = []
        self.stages_mushroom_kingdom_camera_strings = []
        self.stage_spawn_location_strings = {"default": [], "neutral": []}
        self.stage_icon_offsets = []
        self.stage_configs = []
        self.stage_series_textures = {}
        # add_sound(...) strings spliced into src/FGM.asm by the appender.
        self.sound_add_list = []
        # Running FGM/SFX id counter. Seeded by the appender from the character
        # processor's final LAST_SFX_ID so stage sounds continue the sequence.
        self.LAST_SFX_ID = 0
        # Item.add_item(...) strings spliced into src/Item.asm by the appender
        # (one per stage-registered custom item).
        self.item_add_list = []

    def process(self, stage_folder: str) -> None:
        """Process one stage folder and accumulate patch data into self."""
        config = yaml.safe_load(open(
            f"extra_stages/{stage_folder}/config.yaml", encoding="utf-8"
        ))
        print(config)

        output_path = f"build/extra_stages/{stage_folder}/"
        original_path = f"./extra_stages/{stage_folder}/"

        # Copy all files from the stage folder to the output path
        shutil.copytree(
            original_path,
            output_path,
            dirs_exist_ok=True
        )

        # config['collision']: rewrite stage.bin's map-collision geometry from a
        # declarative spec (see smashremix_extra/stage/collision.py). Runs on the
        # build copy before it is registered; header.bin is not affected.
        if config.get("collision"):
            from smashremix_extra.stage import collision as _collision
            info = _collision.apply(
                f"{output_path}/stage.bin", config["collision"],
                int(config['offsets']['stage'][0], 16),
                header_path=f"{output_path}/header.bin",
                groupdata_off=int(config['offsets']['header'][1], 16),
                label=stage_folder)
            logger.info("%s: rewrote collision - %d groups, %d lines, %d vertices "
                        "(+%d B in stage.bin)", stage_folder, info['groups'],
                        info['lines'], info['vertices'], info['bytes_added'])

        # config['rebirth']: [x, y] of the rebirth (revival) platform - the
        # kind-0x20 map object in stage.bin. In-place; header.bin untouched.
        if config.get("rebirth"):
            from smashremix_extra.stage import collision as _collision
            rx, ry = config["rebirth"]
            _collision.set_rebirth(
                f"{output_path}/stage.bin",
                int(config['offsets']['stage'][0], 16), rx, ry, label=stage_folder)
            logger.info("%s: moved rebirth platform to (%s, %s)",
                        stage_folder, rx, ry)

        # blast_zones / camera_bounds / light_angle / magnifying_glass_color ->
        # MPGroundData in header.bin (in-place scalar writes; see ground.py).
        from smashremix_extra.stage import ground as _ground
        _gd_changed = _ground.apply(
            f"{output_path}/header.bin",
            int(config['offsets']['header'][1], 16), config, label=stage_folder)
        if _gd_changed:
            logger.info("%s: patched MPGroundData - %s",
                        stage_folder, ", ".join(_gd_changed))

        # Top-down layout render (collision groups + blast zone + camera bounds
        # + map objects) of the final stage.bin/header.bin, dropped next to them
        # in the build dir for a quick visual sanity check.
        try:
            from smashremix_extra.stage import draw as _draw
            _pngs = _draw.render_group_set(
                f"{output_path}/stage.bin", f"{output_path}/header.bin",
                f"{output_path}/collision_layout.png",
                chain_head=int(config['offsets']['stage'][0], 16),
                groupdata_off=int(config['offsets']['header'][1], 16))
            logger.info("%s: wrote %d collision renders (%s + per-group)",
                        stage_folder, len(_pngs), "collision_layout.png")
        except Exception as _e:                       # never fail a build over a preview
            logger.warning("%s: collision_layout.png render skipped (%s)",
                           stage_folder, _e)

        # Full attribute dump (collision + map objects + every MPGroundData
        # field) as YAML next to the built files, for debugging / diffing.
        try:
            from smashremix_extra.stage import attributes as _attrs
            _attrs.dump_to(
                f"{output_path}/stage_attributes.yaml",
                f"{output_path}/stage.bin", f"{output_path}/header.bin",
                int(config['offsets']['stage'][0], 16),
                int(config['offsets']['header'][1], 16))
            logger.info("%s: wrote stage_attributes.yaml", stage_folder)
        except Exception as _e:                        # never fail a build over a dump
            logger.warning("%s: stage_attributes.yaml skipped (%s)",
                           stage_folder, _e)

        # header_reqlist.txt may reference imported files via ${NAME} tokens that
        # have no matching node in header.bin's resource linked list yet; count
        # them (or take an explicit override) so validate_reqlist() allows it.
        with open(f"{output_path}/header_reqlist.txt", encoding="utf-8") as _f:
            _raw_header_reqlist = _f.read()
        extend_header_reqlist = config.get(
            "extend_header_reqlist",
            len(re.findall(r"\$\{(?!STAGE\}|BACKGROUND\})[A-Za-z0-9_]+\}",
                           _raw_header_reqlist)))

        header_file = FileManager.add_file(
            path=f"{output_path}/header.bin",
            name=f"{stage_folder}_header",
            internal_file_table_offset=config['offsets']['header'][0],
            internal_file_resource_offset=config['offsets']['header'][1],
            reqlist_path=f"{output_path}/header_reqlist.txt",
            compression_level=1,
            extend_reqlist=extend_header_reqlist
        )

        stage_file = FileManager.add_file(
            path=f"{output_path}/stage.bin",
            name=f"{stage_folder}_stage",
            internal_file_table_offset=config['offsets']['stage'][0],
            internal_file_resource_offset=config['offsets']['stage'][1],
            compression_level=2
        )

        bg_file = FileManager.add_file(
            path=f"{output_path}/background.bin",
            name=f"{stage_folder}_background",
            internal_file_table_offset=config['offsets']['background'][0],
            internal_file_resource_offset=config['offsets']['background'][1],
            compression_level=2
        )

        # External bin/sound imports (mirrors the character pipeline). Populates
        # FILE_<NAME> / FGM_<NAME> constants for the stage's hazards.asm and
        # returns {name: file_id} for header_reqlist.txt ${NAME} substitution.
        imported_file_ids = self._process_stage_imports(
            stage_folder, config, output_path, original_path)

        self.stage_headers_strings.append(
            f"constant STAGE_{stage_folder.upper().replace("/", "_")}(0x{header_file.id:X})"
        )

        stage_name = config.get(
            'name', stage_folder.upper().replace("/", " "))

        config["id"] = stage_folder.upper().replace("/", "_")

        if "/" in stage_folder:
            config["base_stage"] = "id.STAGE_" + \
                stage_folder.upper().split("/")[0]
            config["variant_type"] = "variant_type." + \
                stage_folder.split("/")[1].upper()

        self.stage_configs.append(config)

        # Stage icon
        # If stage icon image doesn't exist, generate one
        if not os.path.isfile(f"{output_path}/icon.bmp"):
            create_stage_icon(
                stage_name,
                f"{output_path}/icon.bmp"
            )

        # Append icon
        pixels, w, h = get_image_data(
            f"{output_path}/icon.bmp"
        )
        icon_texture = append_image(
            "scripts/153E.bin",
            "scripts/153E.bin",
            pixels,
            w, h,
            ImageMode.RGBA5551
        )
        icon_texture += 16
        icon_texture += 0x01000000  # Flag to use 2nd file
        icon_texture = f"0x{icon_texture:08X}"

        self.stage_icon_offsets.append(icon_texture)

        # Check for SSS series logo image
        if os.path.isfile(f"{output_path}/series_logo.png"):
            pixels, w, h = get_image_data(
                f"{output_path}/series_logo.png"
            )
            series_texture = append_image(
                "scripts/0014.bin",
                "scripts/0014.bin",
                pixels,
                w, h,
                ImageMode.I8
            )
            series_texture += 16

            # Get logo position from config
            series_x = hex_util.float_to_ieee754_hex(
                config.get("series_position", {}).get("x", 3))
            series_y = hex_util.float_to_ieee754_hex(
                config.get("series_position", {}).get("y", 19))

            self.stage_series_textures[f"{stage_folder}"] = {
                "offset": f"0x{series_texture:X}",
                "x": f"0x{series_x}",
                "y": f"0x{series_y}"
            }

        # Spawn locations
        for category in ["default", "neutral"]:
            spawn = config.get(
                'spawn_locations', {}).get(category, {})

            self.stage_spawn_location_strings[category].append("\n\t".join([
                f"// {stage_folder.upper().replace('/', '_')}",
                f"float32 {int(spawn.get('p1')[0]):+05}, {
                    int(spawn.get('p1')[1]):+05}",
                f"float32 {int(spawn.get('p2')[0]):+05}, {
                    int(spawn.get('p2')[1]):+05}",
                f"float32 {int(spawn.get('p3')[0]):+05}, {
                    int(spawn.get('p3')[1]):+05}",
                f"float32 {int(spawn.get('p4')[0]):+05}, {
                    int(spawn.get('p4')[1]):+05}"
            ]))

        # Toggle to use Mushroom Kingdom's camera
        # It scrolls more rather than trying to stay more centered
        # Good for big stages
        if config.get("mushroom_kingdom_camera"):
            self.stages_mushroom_kingdom_camera_strings.append(
                f"\t\taddiu   at, r0, Stages.id.STAGE_{
                    stage_folder.upper().replace('/', '_')}\n"
                f"\t\tbeq     at, v0, mkingdom_camera\n"
            )

        reqlist_entry_count = 0
        with open(f"{original_path}/header_reqlist.txt", 'r', encoding='utf-8') as reqlist:
            with open(f"{output_path}/header_reqlist.txt", 'w', encoding='utf-8') as compiled_reqlist:
                for line in reqlist.readlines():
                    line = line.replace(
                        "${STAGE}", f"{stage_file.id:04X} {stage_file.name}")

                    line = line.replace(
                        "${BACKGROUND}", f"{bg_file.id:04X} {bg_file.name}")

                    for imp_name, imp_id in imported_file_ids.items():
                        line = line.replace(
                            "${" + imp_name.upper() + "}",
                            f"{imp_id:04X} {stage_folder}_file_{imp_name}")

                    if line.strip() and not line.startswith("END OF"):
                        reqlist_entry_count += 1

                    compiled_reqlist.write(line)

        # Grow the header's external reloc chain. NB: read the *build* copy, not
        # the source - ground.apply() (MPGroundData: camera_bounds / blast_zones
        # / light_angle / magnifying_glass_color / ...) has already patched it in
        # place; re-reading the source here would silently discard those edits.
        # `magnifying_glass_color` is handled entirely by ground.apply() now.
        with open(f"{output_path}/header.bin", 'rb') as binary_file:
            data = bytearray(binary_file.read())

            # Grow the header's external reloc chain so every entry in
            # header_reqlist.txt (including imported ${NAME} files) is loaded and
            # gets a reloc slot - the loader walks this chain once per reqlist
            # entry, so a shorter chain leaves the extra files unloaded.
            res_offset = int(config['offsets']['header'][1], 16)
            self._extend_header_reloc_chain(
                data, res_offset, reqlist_entry_count, stage_folder)

            with open(f"{output_path}/header.bin", 'wb') as binary_file:
                binary_file.write(data)

    @staticmethod
    def _extend_header_reloc_chain(data: bytearray, res_offset: int,
                                   target_count: int, stage_folder: str = ""):
        """Extend a stage header's external reloc linked list (headed at
        `res_offset`) to `target_count` nodes.

        The SSB64 stage loader (lbRelocLoadAndRelocFile) walks this chain once
        per entry in the file's req/idx list, reading one dependency file id per
        node. If header_reqlist.txt has more entries than the header.bin chain
        has nodes, the surplus files are never loaded. Each node is a 4-byte
        LBRelocDesc: [u16 next_node_index][u16 words_num]; next == 0xFFFF ends
        the chain (index = byte offset / 4). New terminator nodes are appended
        at EOF with words_num = 0 (relocated pointer -> dependency base).
        """
        cur = res_offset
        node_count = 0
        while True:
            node_count += 1
            nxt = int.from_bytes(data[cur:cur + 2], 'big')
            if nxt == 0xFFFF:
                break
            cur = nxt * 4
            if node_count > 256 or cur + 4 > len(data):
                raise ValueError(
                    f"{stage_folder}: header.bin reloc chain is malformed "
                    f"(walked {node_count} nodes)")

        for _ in range(target_count - node_count):
            if len(data) % 4 != 0:
                data.extend(b'\x00' * (4 - len(data) % 4))
            new_off = len(data)
            if new_off // 4 > 0xFFFE:
                raise ValueError(f"{stage_folder}: header.bin too large to extend")
            # repoint the current terminator at the new node (keep its words_num)
            data[cur:cur + 2] = (new_off // 4).to_bytes(2, 'big')
            data.extend(b'\xFF\xFF\x00\x00')   # new terminator node
            cur = new_off

        return data

    @staticmethod
    def _model_offsets(output_path, name, footer_name):
        """{const_name: offset} of interesting spots inside a GE model .bin
        (empty for non-GE files). `footer_name` = the _hitbox.bin whose footer
        points into this model, if any."""
        path = f"{output_path}/{name}.bin"
        try:
            data = open(path, "rb").read()
        except OSError:
            return {}
        footer = None
        if footer_name:
            try:
                footer = open(f"{output_path}/{footer_name}.bin", "rb").read()
            except OSError:
                logger.warning("%s: footer %s.bin not found for FILES.%s offsets",
                               name, footer_name, name.upper())
        try:
            from smashremix_extra import ge_bin
            raw = ge_bin.describe_offsets(data, footer)
        except Exception as e:                      # noqa: BLE001
            logger.warning("%s: could not describe offsets (%s)", name, e)
            return {}
        # dedupe by offset, keep the first (most specific) name
        seen, out = set(), {}
        for k, v in raw.items():
            if v not in seen:
                seen.add(v)
                out[k] = v
        return out

    def _process_stage_imports(self, stage_folder, config, output_path, original_path):
        """Register a stage's external bin files (config['files']) and sound
        effects (config['sounds']), mirroring the character pipeline.

        - config['files']: list of [name, tableOffset, resourceOffset] entries.
          Each <name>.bin lives in the stage folder; an optional
          <name>_reqlist.txt beside it is honored. Produces FILES.<NAME> (id) and
          FILES.<NAME>_ptr (RAM-addr word) for the stage's hazards.asm.
        - config['sounds']: {name: {sample_rate, fgm_type, reverb, length}} map.
          Each <name>.aifc lives in extra_stages/<stg>/sounds/. Produces an
          FGM.<NAME> constant and an add_sound(...) entry for src/FGM.asm.

        Both go into `scope FILES { ... }` / `scope FGM { ... }` blocks prepended
        to build/extra_stages/<stg>/hazards.asm (which the appender wraps in
        `scope <STG>_HAZARDS { ... }`), so hazards.asm references them as
        FILES.<NAME> / FGM.<NAME> - the same shape as a character's main.asm
        `scope FGM` / `scope FILE_OFFSETS`.

        Returns {name: file_id} for header_reqlist.txt ${NAME} substitution.
        """
        # Emitted as two nested scopes prepended to the stage's hazards.asm
        # (which the appender wraps in `scope <STG>_HAZARDS { ... }`), mirroring
        # how a character's main.asm gets `scope FGM { ... }` / `scope FILE_OFFSETS`:
        #   FILES.<NAME>       - imported file id           (config.yaml `files:`)
        #   FILES.<NAME>_ptr   - word holding its RAM addr, filled in setup via
        #                        resolve_stage_file(FILES.<NAME>, FILES.<NAME>_ptr)
        #   FGM.<NAME>         - imported sound's FGM id     (config.yaml `sounds:`)
        file_constants = []
        fgm_constants = []
        imported_file_ids = {}

        for entry in config.get("files", []):
            if isinstance(entry, str):
                # Raw hex id passthrough (e.g. reuse of an existing file).
                continue
            # [name, tableOffset, resourceOffset, {opts}]; resourceOffset
            # defaults to "3FFFC" (empty resource list) for self-contained model
            # bins. opts: {footer: <name>} names the <name>_hitbox.bin whose
            # ITAttributes footer targets this model (so the MObjSub / sprite
            # offsets can be resolved for the FILES.<NAME>.* constants).
            name = entry[0]
            tbl_off = entry[1] if len(entry) > 1 else "3FFFC"
            res_off = entry[2] if len(entry) > 2 else "3FFFC"
            opts = entry[3] if len(entry) > 3 and isinstance(entry[3], dict) else {}

            reqlist_path = f"{original_path}/{name}_reqlist.txt"
            has_reqlist = os.path.exists(reqlist_path)

            import_file = FileManager.add_file(
                path=f"{output_path}/{name}.bin",
                name=f"{stage_folder}_file_{name}",
                internal_file_table_offset=tbl_off,
                internal_file_resource_offset=res_off,
                reqlist_path=(
                    f"{output_path}/{name}_reqlist.txt" if has_reqlist else None),
                compression_level=1
            )

            imported_file_ids[name] = import_file.id
            up = name.upper()

            # FILES.<NAME>.id   - file id
            # FILES.<NAME>.ptr  - word; setup fills it with the file's RAM addr
            #                     via resolve_stage_file(FILES.<NAME>.id, .ptr)
            # FILES.<NAME>.<x>  - byte offsets inside the .bin (GE self-reloc
            #                     model only): head / obj0.. / tex0.. / tlut0.. /
            #                     mobjsub / sprites / sprite0.. - regenerated on
            #                     every GE re-export so ASM never hard-codes them.
            lines = [f"    scope {up} {{",
                     f"        constant id(0x{import_file.id:X})",
                     f"        OS.align(16)",
                     f"        ptr:",
                     f"        dw 0"]
            for oname, ooff in self._model_offsets(
                    output_path, name, opts.get("footer")).items():
                lines.append(f"        constant {oname}(0x{ooff:X})")
            lines.append("    }")
            file_constants.append("\n".join(lines))

        sounds = config.get("sounds", {}) or {}

        # Compile any sounds/<name>.wav -> <name>.aifc before they're picked up
        # below. Target rate comes from the sound's `sample_rate:` (default
        # 16000, matching add_sound's default) so playback pitch stays correct.
        from smashremix_extra.audio import vadpcm
        vadpcm.convert_dir(
            f"{output_path}/sounds",
            rate_for=lambda n: (sounds.get(n) or {}).get("sample_rate", 16000))

        for name, settings in sounds.items():
            settings = settings or {}
            sample_rate = settings.get("sample_rate", 16000)
            fgm_type = settings.get("fgm_type", "VOICE")
            reverb = settings.get("reverb", 0)
            length = settings.get("length", -1)

            self.LAST_SFX_ID += 1
            fgm_constants.append(
                f"    constant {name.upper()}(0x{self.LAST_SFX_ID:04X})")
            self.sound_add_list.append(
                f"add_sound(../{output_path}sounds/{name}, "
                f"SAMPLE_RATE_{sample_rate}, FGM_TYPE_{fgm_type}, {reverb}, {length})"
            )

        # config['items']: names of item scopes defined inside hazards.asm. Each
        # must supply the constants Item.add_item(item) expects (SPAWN_ITEM,
        # SHOW_GFX_WHEN_SPAWNED, PICKUP_ITEM_MAIN/INIT, DROP_ITEM, THROW_ITEM,
        # PLAYER_COLLISION, item_info_array, ITEM_INFO_ARRAY_ORIGIN). The
        # appender splices the Item.add_item(...) call into src/Item.asm after
        # the last add_item(...) so it runs with the Item scope defined.
        scope_prefix = f"Hazards.{stage_folder.upper().replace('/', '_')}_HAZARDS"
        for item_scope in config.get("items", []) or []:
            self.item_add_list.append(
                f"add_item({scope_prefix}.{item_scope})")

        # Compile ${NAME} tokens in each imported file's own reqlist (e.g. a
        # hitbox footer's <name>_hitbox_reqlist.txt referencing ${MODEL}) into
        # "<id> <file_name>" lines, mirroring the character pipeline. Runs after
        # every file is registered so cross-references resolve regardless of the
        # order they appear in config['files'].
        for name in imported_file_ids:
            compiled_path = f"{output_path}/{name}_reqlist.txt"
            if not os.path.exists(compiled_path):
                continue
            with open(compiled_path, "r", encoding="utf-8") as _f:
                lines = _f.readlines()
            with open(compiled_path, "w", encoding="utf-8") as _f:
                for line in lines:
                    for dep_name, dep_id in imported_file_ids.items():
                        line = line.replace(
                            "${" + dep_name.upper() + "}",
                            f"{dep_id:04X} {stage_folder}_file_{dep_name}")
                    _f.write(line)

        if file_constants or fgm_constants:
            hazards_path = f"{output_path}/hazards.asm"
            if not os.path.exists(f"{original_path}/hazards.asm"):
                print(
                    f"WARNING: {stage_folder} declares files/sounds but has no "
                    f"hazards.asm; the FILES./FGM. scopes will not be compiled.")
            existing = ""
            if os.path.exists(hazards_path):
                with open(hazards_path, 'r', encoding='utf-8') as _f:
                    existing = _f.read()

            blocks = ["// Auto-generated stage imports (do not edit) - "
                      "config.yaml files: / sounds:"]
            if file_constants:
                blocks.append(
                    "scope FILES {\n" + "\n".join(file_constants) + "\n}")
            if fgm_constants:
                blocks.append(
                    "scope FGM {\n" + "\n".join(fgm_constants) + "\n}")

            with open(hazards_path, 'w', encoding='utf-8') as _f:
                _f.write("\n".join(blocks))
                _f.write("\n\n")
                _f.write(existing)

        return imported_file_ids


