import lineinfile
import os
import sys
import shutil
import yaml
import re
import argparse
from pathlib import Path

from smashremix_extra.image_appender import append_image, get_image_data, ImageMode
from smashremix_extra.constants import SMASHREMIX_PATH as smashremix_path, VARIANT_TYPES
from smashremix_extra.asm_util import add_to_scope, add_to_scope_on_empty, add_to_label, add_to_label_on_empty
from smashremix_extra.rom_util import run_windows_command, extract_files
from smashremix_extra.smashremix.kirbyshared import kirby_shared
from smashremix_extra.logger import logger
from smashremix_extra import file_manager
from smashremix_extra.character.processor import CharacterProcessor
from smashremix_extra.character.bio_overflow import apply_bio_overflow_patches, write_bio_overflow_asm
from smashremix_extra.stage.processor import StageProcessor
from smashremix_extra.audio.processor import AudioProcessor
from smashremix_extra.injector import ROMInjector, MODIFIED_FILES


class CharacterAppender:
    def __init__(self, args):
        if not os.path.exists(os.path.join(smashremix_path, "src/File.asm")):
            print("\n"
                  f"ERROR: Smash Remix source code not found in '{smashremix_path}' folder. "
                  f"Initialize submodules (Git) or download source manually.")
            sys.exit(1)

        if not os.path.exists(os.path.join(smashremix_path, "roms/ssb.rom")):
            print("\n"
                  "ERROR: Vanilla SSB64 USA ROM titled 'ssb.rom' not in 'smashremix/roms' folder!")
            sys.exit(1)

        if not os.path.exists(os.path.join(smashremix_path, "roms/original.z64")):
            print("original.z64 not found. Trying to apply patch...")
            xdelta_exe = os.path.join(smashremix_path, "xdelta.exe")
            ssb_rom = os.path.join(smashremix_path, "roms", "ssb.rom")
            original_xdelta = os.path.join(smashremix_path, "original.xdelta")
            original_z64 = os.path.join(
                smashremix_path, "roms", "original.z64")
            result = run_windows_command(
                f'{xdelta_exe} -d -f -s {ssb_rom} {original_xdelta} {original_z64}'
            )
            if result.returncode != 0:
                print(result.stderr)
            print("OK")

        extract_files(smashremix_path)

        default_nameplate_pixels, w, h = get_image_data(
            "extra_resources/nameplate_default.png")
        name_texture_offset = append_image(
            "scripts/0011.bin", "scripts/0011.bin",
            default_nameplate_pixels, w, h, ImageMode.IA8
        )
        self.name_texture_default = f"0x{name_texture_offset:08X} + 0x10"

        default_sp_icon_pixels, w, h = get_image_data(
            "extra_resources/1p_icon.png")
        sp_icon_offset = append_image(
            "scripts/000B.bin", "scripts/000B.bin",
            default_sp_icon_pixels, w, h, ImageMode.RGBA5551
        )
        self.sp_icon_default = f"0x{sp_icon_offset:08X} + 0x10"

        self.char_folders = [cf for cf in os.listdir("extra_characters") if os.path.isdir(
            os.path.join("extra_characters", cf)) and not cf.startswith("_")]
        self.char_folders.sort()

        if args.single_character:
            self.char_folders = [args.single_character]

        self.stage_folders = os.listdir("extra_stages")
        self.stage_folders = [
            sf for sf in self.stage_folders if not sf.startswith("_")]

        # Check for stage variants
        self.stage_variants = []
        for sf in self.stage_folders:
            for variant_type in VARIANT_TYPES:
                variant_path = f"extra_stages/{sf}/{variant_type}"
                if os.path.isdir(variant_path):
                    self.stage_variants.append(f"{sf}/{variant_type}")

        self.stage_folders.extend(self.stage_variants)

        self.stage_folders.sort()

        if args.single_character:
            self.stage_folders = []

        if args.single_stage:
            # Build with only this stage (plus its variants) and no characters.
            variants = [
                v for v in self.stage_folders
                if v.startswith(f"{args.single_stage}/")
            ]
            self.stage_folders = [args.single_stage] + variants
            self.char_folders = []

        print(f"Extra characters: {len(self.char_folders)}")
        print(f"Extra stages: {len(self.stage_folders)}")

        self.audio_proc = AudioProcessor()

        last_sfx_id = 694  # Last vanilla SFX ID

        self.injected_file_num = 0

        # Count Remix's last FGM ID
        with open(os.path.join(smashremix_path, "src/FGM.asm"), 'r', encoding='utf-8') as file:
            for line in file:
                stripped_line = line.lstrip()
                if stripped_line.startswith('add_sound') or stripped_line.startswith('add_fgm'):
                    last_sfx_id += 1

        last_remix_sfx_id = last_sfx_id

        # Count Remix's last SwordTrail ID
        sword_trail_count = sum(
            1 for line in open(os.path.join(smashremix_path, 'src/SwordTrail.asm'), encoding='utf-8')
            if re.match(r'^\s*add_sword_trail\(', line))

        self.char_proc = CharacterProcessor(
            name_texture_default=self.name_texture_default,
            sp_icon_default=self.sp_icon_default,
            last_sfx_id=last_sfx_id,
            last_remix_sfx_id=last_remix_sfx_id,
            sword_trail_count=sword_trail_count,
            characters_exist=self.char_folders,
            stage_ids=self._collect_stage_ids(),
        )
        self.stage_proc = StageProcessor()

        # Delete the build folder if it exists and create a new one
        if os.path.exists("build"):
            shutil.rmtree("build")
        os.makedirs("build", exist_ok=True)

    def _collect_stage_ids(self) -> set:
        """Build the set of every valid Stages.id.X name: vanilla/remix
        stages (parsed from src/Stages.asm's `scope id { ... }` block)
        plus every extra stage folder we're about to add (using the same
        STAGE_<FOLDER> naming StageProcessor generates for them)."""
        stage_ids = set()

        stages_asm_path = "src/Stages.asm"
        if os.path.exists(stages_asm_path):
            with open(stages_asm_path, encoding="utf-8") as f:
                content = f.read()

            start = content.find("scope id {")
            if start != -1:
                depth = 0
                end = start
                for i, ch in enumerate(content[start:], start=start):
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            end = i
                            break
                block = content[start:end]
                stage_ids.update(re.findall(r"constant\s+(\w+)\s*\(", block))
        else:
            logger.warning(
                f"Could not find {stages_asm_path} to validate character singleplayer stage references against; "
                "only extra stages will be treated as valid."
            )

        for sf in self.stage_folders:
            stage_ids.add(f"STAGE_{sf.upper().replace('/', '_')}")

        return stage_ids

    def prepare_files(self):
        """Process all character and stage folders, registering files with FileManager."""
        for character_folder in self.char_folders:
            self._process_character(character_folder)

        # Continue the FGM/SFX id sequence for stage sound imports.
        self.stage_proc.LAST_SFX_ID = self.char_proc.LAST_SFX_ID

        for stage_folder in self.stage_folders:
            self._process_stage(stage_folder)

    def _process_character(self, character_folder):
        self.char_proc.process(character_folder)

    def _process_stage(self, stage_folder):
        self.stage_proc.process(stage_folder)

    def inject_files_in_rom(self):
        if not self.char_folders and not self.stage_folders:
            return

        injector = ROMInjector(
            input_rom=os.path.join(smashremix_path, "roms/original.z64"),
            output_rom=os.path.join(
                smashremix_path, "roms/original_extra.z64"),
        )

        for file_id, path, tbl_off, res_off, level in MODIFIED_FILES:
            injector.modify(file_id, path, tbl_off, res_off,
                            compression_level=level)

        for import_file in file_manager.FileManager.get_import_files():
            injector.add(
                file_path=import_file.path,
                tbl_offset=import_file.internal_file_table_offset,
                res_offset=import_file.internal_file_resource_offset,
                reqlist_path=import_file.reqlist_path,
                compression_level=import_file.compression_level,
            )

        injector.save(on_progress=print)

    def copy_remix_src(self):
        # Walk through all directories and files in the root folder
        print("Copying original source code into src/...")

        # Delete src/ folder if it exists
        if os.path.exists("src"):
            shutil.rmtree("src")

        os.makedirs("src", exist_ok=True)

        shutil.copytree(
            os.path.join(smashremix_path, "src"),
            "src",
            dirs_exist_ok=True,
        )

        shutil.copy(
            os.path.join(smashremix_path, "main.asm"),
            "main.asm"
        )

    def overwrite_files(self):
        # Overwrite src/ with smashremix_overwrite/. Resolved relative to this
        # file rather than CWD, since smashremix_overwrite ships alongside
        # character_appender.py and callers may run this from a different
        # working directory (e.g. a content repo that pulls this in as a
        # submodule, with its own extra_characters/build/src at CWD).
        print("Overwriting source code with custom files...")

        smashremix_overwrite_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "smashremix_overwrite")

        shutil.copytree(
            smashremix_overwrite_path,
            "src",
            dirs_exist_ok=True
        )

    def edit_src_files(self):
        original_size = os.path.getsize(os.path.join(
            smashremix_path, "roms/original_extra.z64"))
        self._patch_src_paths(original_size)
        self._patch_audio_asm(original_size)
        self._patch_character_asm()
        self._patch_stage_asm()
        self._patch_toggle_asm()

    def _patch_src_paths(self, original_size):
        # main.asm
        # In all .asm files in src/, replace paths.
        # These paths get embedded as text for the assembler (bass) to resolve
        # at assembly time, run with CWD at the content repo root - so unlike
        # smashremix_path's own (possibly absolute, __file__-anchored) value
        # used for this script's own file I/O, what we embed here must be a
        # path relative to CWD, or bass ends up trying to resolve an absolute
        # path relative to itself.
        smashremix_path_relative = os.path.relpath(
            smashremix_path, os.getcwd())
        asm_files = []

        for root, dirs, files in os.walk("src"):
            for file in files:
                if file.endswith(".asm"):
                    filepath = os.path.join(root, file)

                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()

                    content = content.replace(
                        "../build/", f"../{smashremix_path_relative}/build/")
                    content = content.replace(
                        "roms/original.z64", f"{smashremix_path_relative}/roms/original_extra.z64")

                    with open(filepath, 'w', encoding='utf-8') as f:
                        f.write(content)

        with open("main.asm", 'r', encoding='utf-8') as f:
            main_content = f.read()

        main_content = main_content.replace(
            "assembler/", f"{smashremix_path_relative}/assembler/")
        main_content = main_content.replace(
            "roms/original.z64", f"{smashremix_path_relative}/roms/original_extra.z64")

        with open("main.asm", 'w', encoding='utf-8') as f:
            f.write(main_content)

        # Insert new character asm files before Kirby
        lineinfile.add_line_to_file(
            filepath="main.asm",
            line="// Extra characters\n"+"\n".join(
                [f'include "build/extra_characters/{name}/main.asm"' for name in self.char_folders]),
            inserter=lineinfile.BeforeFirst(r"\/\/ KIRBY")
        )

        # Update internal name
        lineinfile.add_line_to_file(
            filepath="main.asm",
            line='db "REMIX EXTRA"',
            regexp=r'db\s+"SMASH REMIX"'
        )

        lineinfile.add_line_to_file(
            filepath="main.asm",
            line=f'origin 0x{original_size:08X}',
            regexp=r'origin\s+0x[0-9A-Fa-f]{8}'
        )

        # Extend file table size
        lineinfile.add_line_to_file(
            filepath="main.asm",
            line="fill 0x800",
            regexp=r'fill .*',
            inserter=lineinfile.AfterLast(r"file_table:.*\n")
        )

        # Boot.asm
        original_size_str = f'{original_size:08X}'

        lineinfile.add_line_to_file(
            filepath="src/Boot.asm",
            line=f'\t\tlui\t\ta0, 0x{original_size_str[:4]}',
            regexp=r'.*load rom address.*'
        )

        # Get version string
        version_string = ""
        with open("src/Boot.asm", 'r') as f:
            for line in f:
                m = re.match(
                    r'.*string_version:; String.insert\(\"(.*)\"\)', line)
                if m:
                    version_string = m.group(1)
                    break

        version_txt_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "version.txt")
        with open(version_txt_path, 'r', encoding='utf-8') as f:
            extra_version = f.read()
        lineinfile.add_line_to_file(
            filepath="src/Boot.asm",
            line=f'\tstring_version:; String.insert("{version_string} {extra_version}")',
            regexp=r'.*string_version.*'
        )

    def _patch_audio_asm(self, original_size):
        self.audio_proc.load()
        self.audio_proc.patch_midi_asm(
            original_size,
            self.char_proc.victory_theme_strings,
            self.char_proc.midi_priority_overrides,
            self.char_proc.midi_bend_range_overrides,
            self.char_proc.midi_master_volume_overrides,
        )

    def _patch_character_asm(self):
        # File.asm
        lineinfile.add_line_to_file(
            filepath="src/File.asm",
            line="\n".join(
                [f"\tconstant {import_file.name.upper()}(0x{import_file.id:04X})" for import_file in file_manager.FileManager.get_import_files()]),
            inserter=lineinfile.AfterLast(r"constant")
        )

        # filename_overrides.txt
        shutil.copy(
            os.path.join(smashremix_path, "roms/filename_overrides.txt"),
            os.path.join(smashremix_path, "roms/filename_overrides_extra.txt")
        )

        lineinfile.add_line_to_file(
            filepath=os.path.join(
                smashremix_path, "roms/filename_overrides_extra.txt"),
            line="\n".join(
                [f"{import_file.id:04X} {import_file.name.upper()}" for import_file in file_manager.FileManager.get_import_files()]),
            inserter=lineinfile.BeforeLast(r"^\s*$")
        )

        # Character.asm
        # Get ADD_CHARACTERS value
        add_characters_value = 0
        with open("src/Character.asm", 'r', encoding='utf-8') as f:
            for line in f:
                m = re.match(r'.*constant ADD_CHARACTERS\((.*)\)', line)
                if m:
                    add_characters_value = int(m.group(1))
                    break

        lineinfile.add_line_to_file(
            filepath="src/Character.asm",
            line=f'\tconstant ADD_CHARACTERS({
                add_characters_value+len(self.char_folders)})',
            regexp=r'.*constant ADD_CHARACTERS\(.*\)'
        )

        lineinfile.add_line_to_file(
            filepath="src/Character.asm",
            line="\t"+"\n\t".join(self.char_proc.character_defs),
            inserter=lineinfile.AfterLast(r".*ADD NEW CHARACTERS HERE")
        )

        # CharacterSelect.asm
        lineinfile.add_line_to_file(
            filepath="src/CharacterSelect.asm",
            line="\n".join(
                [f"\tdb Character.id.{name.upper()}" for name in self.char_proc.bonus_chars]),
            inserter=lineinfile.AfterFirst(r"db Character.id.PIANO")
        )

        num_bonus_chars = 0
        with open("src/CharacterSelect.asm", 'r') as f:
            for line in f:
                m = re.match(r'.*constant NUM_BONUS_CHARS\((.*)\)', line)
                if m:
                    num_bonus_chars = int(m.group(1))
                    break

        lineinfile.add_line_to_file(
            filepath="src/CharacterSelect.asm",
            line=f'\tconstant NUM_BONUS_CHARS({
                num_bonus_chars+len(self.char_proc.bonus_chars)})',
            regexp=r'.*constant NUM_BONUS_CHARS\(.*\)'
        )

        main_sizes = [
            f"dw {hex(os.path.getsize("./build/extra_characters/"+char_folder+"/character.bin"))} + 0x200 // {char_folder.upper()}" for char_folder in self.char_folders
        ]

        lineinfile.add_line_to_file(
            filepath="src/CharacterSelect.asm",
            line="\t"+"\n\t".join(main_sizes),
            inserter=lineinfile.AfterFirst(r".*ADD NEW CHARACTERS HERE")
        )

        model_req_strings = [
            f"add_alt_req_list(Character.id.{character.upper()}, ../build/extra_characters/{character}/MODEL_REQ)" for character in self.char_folders
        ]

        lineinfile.add_line_to_file(
            filepath="src/CharacterSelect.asm",
            line="\n\t// EXTRA\n\t"+"\n\t".join(model_req_strings),
            inserter=lineinfile.AfterLast(r".*add_alt_req_list\(Character.*")
        )

        lineinfile.add_line_to_file(
            filepath="src/CharacterSelect.asm",
            line="\t"+"\n\t".join(self.char_proc.add_to_css_strings),
            inserter=lineinfile.AfterLast(r".*ADD NEW CHARACTERS HERE")
        )

        # # Sonic's alt_malloc_table entry sums his main model (0x16320) and Classic Sonic's model
        # # (0x170F0); the 0x22260 term doesn't correspond to any known file and is dropped here.
        # lineinfile.add_line_to_file(
        #     filepath="src/CharacterSelect.asm",
        #     line=f'\tdw  0x16320 + 0x200 + 0x170F0 + 0x200 // 0x3B - SONIC',
        #     regexp=r'.*0x16320 \+ 0x22260 \+ 0x170E8 \+ 0x200.*'
        # )

        # Increase dynamic css heap size
        lineinfile.add_line_to_file(
            filepath="src/CharacterSelect.asm",
            line=f'\t\tconstant HEAP_SIZE(0x0001D000)',
            regexp=r'.*constant HEAP_SIZE\(.*\)'
        )

        lineinfile.add_line_to_file(
            filepath="src/CharacterSelect.asm",
            line=f'\t\tconstant ACTIVE_HEAP_COUNT(5)',
            inserter=lineinfile.AfterLast(r'.*constant HEAP_SIZE\(.*\)')
        )

        # Change ram threshold
        lineinfile.add_line_to_file(
            filepath="src/CharacterSelect.asm",
            line=f'\t\tlui t7, 0x8050 // t7 = ram threshold',
            regexp=r'\s*lui\s*t7, 0x8078'
        )

        lineinfile.add_line_to_file(
            filepath="src/CharacterSelect.asm",
            line=f'\t\tlui t7, 0x8050 // t7 = ram threshold',
            regexp=r'\s*lui\s*t7, 0x8078'
        )

        for _ in range(2):
            lineinfile.add_line_to_file(
                filepath="src/CharacterSelect.asm",
                line=(f'\t\tlli s2, dynamic_css.ACTIVE_HEAP_COUNT '
                      '// s2 = loop index'),
                regexp=r'\s*lli\s+s2, 0x0008\s+// s2 = loop index'
            )

        for _ in range(2):
            lineinfile.add_line_to_file(
                filepath="src/CharacterSelect.asm",
                line=(f'\t\tlli t2, dynamic_css.ACTIVE_HEAP_COUNT - 1 '
                      '// t2 = times to loop - 1'),
                regexp=r'\s*lli\s+t2, 0x0007\s+// t2 = times to loop - 1'
            )

        # clear_first_obsolete_heap_slot_'s eviction search has no bound of its own - it walks past
        # heap_slot_0 looking for a slot not currently claimed by a player, trusting that with 8
        # slots and at most 4 players one is always spare. With only ACTIVE_HEAP_COUNT slots actually
        # initialized above, if it didn't find a candidate in range it would fall through into
        # heap_slot_4+, whose backing heap_struct was never given a real floor/ceiling (they're
        # left zeroed), so malloc would then hand out memory from a null heap - this is what was
        # causing the malloc errors/instability with 4 players on the CSS at once.
        lineinfile.add_line_to_file(
            filepath="src/CharacterSelect.asm",
            line="\t\t"+"\n\t\t".join(['slti    at, v0, dynamic_css.ACTIVE_HEAP_COUNT // at = 1 if v0 is still a real slot',
                                       'beqz    at, _out_of_slots // if v0 ran past the real slots, stop and fall back instead of reading garbage']),
            inserter=lineinfile.AfterFirst(
                r'addiu\s+v0,\s*v0,\s*0x0001\s+// v0 = next heap slot')
        )

        # If no obsolete slot is found, fall back to a second pass that only enforces the hard
        # safety rule (never evict a slot that's char_id-matched to one of the 4 currently active
        # players, t1-t4) and drops the softer "was used last frame" anti-flicker rule. With
        # ACTIVE_HEAP_COUNT(5) real slots holding at most 4 distinct active character ids, at least
        # one slot can never match t1-t4, so this pass is guaranteed to find a genuinely safe slot
        # instead of corrupting a live one. _forced_zero is an unreachable safety net in case that
        # guarantee is ever violated.
        lineinfile.add_line_to_file(
            filepath="src/CharacterSelect.asm",
            line="\t\t"+"\n\t\t".join([
                '_out_of_slots:',
                'li      t0, dynamic_css.heap_slot_0',
                'addiu   v0, r0, -0x0001',
                '_loop_2:',
                'addiu   v0, v0, 0x0001   // v0 = next heap slot',
                'slti    at, v0, dynamic_css.ACTIVE_HEAP_COUNT // at = 1 if v0 is still a real slot',
                'beqz    at, _forced_zero // unreachable in practice; force-reuse slot 0 rather than read past the array',
                'lw      at, 0x0004(t0)   // at = slot\'s char_id',
                'beql    at, t1, _loop_2  // if slot\'s char_id still in use, skip',
                'addiu   t0, t0, 0x0010   // t0 = next heap slot address',
                'beql    at, t2, _loop_2',
                'addiu   t0, t0, 0x0010',
                'beql    at, t3, _loop_2',
                'addiu   t0, t0, 0x0010',
                'beql    at, t4, _loop_2',
                'addiu   t0, t0, 0x0010',
                'b       _clear',
                'nop',
                '_forced_zero:',
                'lli     v0, 0x0000 // v0 = 0'
            ]),
            inserter=lineinfile.BeforeFirst(r'\s*_clear:')
        )

        # Character Data uses its own heap recycler
        lineinfile.add_line_to_file(
            filepath="src/CharacterSelect.asm",
            line="\t\t"+"\n\t\t".join([
                'OS.read_byte(Global.current_screen, t7)',
                'lli     t8, Global.screen.DATA_CHARACTERS',
                'beq     t7, t8, _normal_load',
                'nop'
            ]),
            inserter=lineinfile.BeforeLast(r".*// Custom heap Logic:")
        )

        idStrings = []
        offsetStrings = []
        xyStrings = []
        dwStrings = []

        LAST_SERIES_ID = 0
        with open("src/CharacterSelect.asm", 'r', encoding='utf-8') as f:
            lines = f.readlines()

            in_scope = False
            constant = ''

            for i, line in enumerate(lines):
                if "scope series_logo" in line:  # Detect the scope
                    in_scope = True

                if in_scope and 'constant' in line:  # Detect the last constant
                    constant = line

                if in_scope and line.strip() == '':  # Update id with last constant's value if line is empty
                    match = re.search(r'(\d[0-9]*)', constant)
                    self.LAST_SERIES_ID = int(match.group(0))
                    break

        for cf, series_texture in self.char_proc.character_series_textures.items():
            self.LAST_SERIES_ID += 1
            idStrings.append(
                f'constant {cf.upper()}({self.LAST_SERIES_ID})'
            )

            offsetStrings.append(
                f'constant {cf.upper()}({series_texture.get("offset")})'
            )

            xyStrings.append(
                f'constant X_{cf.upper()}({series_texture.get("x")})'
            )

            xyStrings.append(
                f'constant Y_{cf.upper()}({series_texture.get("y")})'
            )

            dwStrings.append(
                f'dw offset.{cf.upper()}, position.X_{cf.upper()}, position.Y_{cf.upper()}'
            )

        for sf, series_texture in self.stage_proc.stage_series_textures.items():
            self.LAST_SERIES_ID += 1
            idStrings.append(
                f'constant {sf.upper()}({self.LAST_SERIES_ID})'
            )

            offsetStrings.append(
                f'constant {sf.upper()}({series_texture.get("offset")})'
            )

            xyStrings.append(
                f'constant X_{sf.upper()}({series_texture.get("x")})'
            )

            xyStrings.append(
                f'constant Y_{sf.upper()}({series_texture.get("y")})'
            )

            dwStrings.append(
                f'dw offset.{sf.upper()}, position.X_{sf.upper()}, position.Y_{sf.upper()}'
            )

        add_to_scope_on_empty("src/CharacterSelect.asm",
                              "scope series_logo", idStrings)
        add_to_scope("src/CharacterSelect.asm", "scope offset", offsetStrings)
        add_to_scope("src/CharacterSelect.asm", "scope position", xyStrings)

        lineinfile.add_line_to_file(
            filepath="src/CharacterSelect.asm",
            line="\t\t"+"\n\t\t".join(dwStrings),
            inserter=lineinfile.AfterLast(
                r"^\s*(dw offset)\.*")
        )

        add_to_scope("src/CharacterSelect.asm", "scope portrait_offsets",
                     ["// extra"] + self.char_proc.character_portrait_defs)

        offsetStrings = []
        variantStrings = []
        for cf, variant_icon in self.char_proc.css_dpad_icons.items():
            offsetStrings.append(
                f"constant {cf.upper()}(0x{variant_icon:0X} + 0x10)"
            )

            variantStrings.append(
                f"\t\tlli     t2, Character.id.{cf.upper()}\n"
                f"\t\tbeql    a1, t2, _draw_icon          // If {cf.upper()}, then draw {cf.upper()} stock icon\n"
                f"\t\taddiu   a1, at, VARIANT_ICON_OFFSET.{cf.upper()} // a1 = {cf.upper()} footer struct"
            )

        add_to_scope("src/CharacterSelect.asm",
                     "scope VARIANT_ICON_OFFSET", offsetStrings)

        lineinfile.add_line_to_file(
            filepath="src/CharacterSelect.asm",
            line="\n".join(variantStrings),
            inserter=lineinfile.AfterLast(r".*// a1 = ROY footer struct")
        )

        # CharacterDataScreen.asm
        lineinfile.add_line_to_file(
            filepath="src/CharacterDataScreen.asm",
            line="\t// Extra characters\n\t" +
            "\n\t".join(self.char_proc.character_data_screen_defs) +
            "\n\t",
            inserter=lineinfile.BeforeLast(
                r".*extend_tables\(\)")
        )

        lineinfile.add_line_to_file(
            filepath="src/CharacterDataScreen.asm",
            line="\t// Extra characters\n\t" +
            "\n\t".join(self.char_proc.character_data_screen_order),
            inserter=lineinfile.AfterLast(
                r".*set_char_order\(.*\)")
        )

        lineinfile.add_line_to_file(
            filepath="src/CharacterDataScreen.asm",
            line="\t\t" +
            "\n\t\t".join(self.char_proc.data_screen_big_border_defs),
            inserter=lineinfile.AfterLast(
                r".*// branch to use large border")
        )

        self.audio_proc.patch_bgm_asm()

        # FGM.asm - character sounds first, then stage sounds (matches the order
        # LAST_SFX_ID was advanced, so the generated FGM ids stay correct).
        lineinfile.add_line_to_file(
            filepath="src/FGM.asm",
            line="\t"+"\n\t".join(
                self.char_proc.sound_add_list + self.stage_proc.sound_add_list),
            inserter=lineinfile.AfterLast(
                r"^\s*(add_sound|add_fgm|add_sound_advanced)\(.*")
        )

        # resultsscreen.asm
        lineinfile.add_line_to_file(
            filepath="src/resultsscreen.asm",
            line="\t"+"\n\t".join(self.char_proc.results_screen_defs),
            inserter=lineinfile.AfterLast(r".*ADD NEW CHARACTERS HERE")
        )

        lineinfile.add_line_to_file(
            filepath="src/resultsscreen.asm",
            line="\t\t"+"\n\t\t".join(self.char_proc.results_j_win_defs),
            inserter=lineinfile.AfterLast(r".*// a0 = offset to \"WIN!\"")
        )

        offsetStrings = []
        zoomStrings = []
        colorStrings = []

        for cf, series_model in self.char_proc.character_series_models.items():
            offsetStrings.append(
                f'constant {cf.upper()}({series_model.get("offset")})'
            )

            zoomStrings.append(
                f'constant {cf.upper()}({series_model.get("zoom")})'
            )

            colorStrings.append(
                f'constant {cf.upper()}({series_model.get("color")})'
            )

        add_to_scope("src/resultsscreen.asm", "series_logo:", offsetStrings)
        add_to_scope("src/resultsscreen.asm", "series_logo_zoom:", zoomStrings)
        add_to_scope("src/resultsscreen.asm",
                     "series_logo_color:", colorStrings)

        # yoshishared.asm
        # yoshi_jump
        lineinfile.add_line_to_file(
            filepath="src/yoshishared.asm",
            line="\t"+"\n\t".join(self.char_proc.yoshi_jump_defs),
            inserter=lineinfile.AfterLast(
                r".*beq     a0, v0, _yoshi_dj_1")
        )
        # yoshi_shield
        _shield_inserter_patterns = [
            r".*beq     t7, at, _yoshi_shield_1",
            r".*beq     t9, at, _yoshi_shield_2",
            r".*beq     t6, at, _yoshi_shield_3",
            r".*beq     t8, at, _yoshi_shield_4",
            r".*beq     t1, at, _yoshi_shield_5",
            r".*beq     t7, at, _yoshi_shield_6",
            r".*beq     v1, at, _yoshi_shield_7",
            r".*beq     t8, at, _yoshi_shield_8",
        ]
        for i, pattern in enumerate(_shield_inserter_patterns):
            lineinfile.add_line_to_file(
                filepath="src/yoshishared.asm",
                line="\t"+"\n\t".join(self.char_proc.yoshi_shield_defs[i]),
                inserter=lineinfile.AfterLast(pattern)
            )
        # yoshi_grab
        lineinfile.add_line_to_file(
            filepath="src/yoshishared.asm",
            line="\t"+"\n\t".join(self.char_proc.yoshi_grab1_defs),
            inserter=lineinfile.AfterLast(
                r".*beq     at, v0, _yoshi_grab_1")
        )
        lineinfile.add_line_to_file(
            filepath="src/yoshishared.asm",
            line="\t"+"\n\t".join(self.char_proc.yoshi_grab2_defs),
            inserter=lineinfile.AfterLast(
                r".*beq     at, v0, _yoshi_grab_2")
        )
        lineinfile.add_line_to_file(
            filepath="src/yoshishared.asm",
            line="\t"+"\n\t".join(self.char_proc.yoshi_throw1_defs),
            inserter=lineinfile.AfterLast(
                r".*beq     at, v0, _yoshi_throw_1")
        )
        # yoshi_recover
        lineinfile.add_line_to_file(
            filepath="src/yoshishared.asm",
            line="\t"+"\n\t".join(self.char_proc.yoshi_recover_defs),
            inserter=lineinfile.AfterLast(
                r".*beq     at, v0, _yoshi_recover_1")
        )
        # yoshi_upspecial
        lineinfile.add_line_to_file(
            filepath="src/yoshishared.asm",
            line="\t"+"\n\t".join(self.char_proc.yoshi_upspecial_defs),
            inserter=lineinfile.AfterLast(
                r".*li      a1, upspecial_struct_jyoshi     // JYOSHI File Pointer placed in correct location")
        )
        lineinfile.add_line_to_file(
            filepath="src/yoshishared.asm",
            line="\t"+"\n\t".join(self.char_proc.yoshi_upspecialstruct_defs),
            inserter=lineinfile.BeforeLast(
                ".*}")
        )
        # yoshi_downspecial
        lineinfile.add_line_to_file(
            filepath="src/yoshishared.asm",
            line="\t"+"\n\t".join(self.char_proc.yoshi_downspecial_defs),
            inserter=lineinfile.AfterLast(
                r".*li      a1, downspecial_struct_jyoshi   // JYOSHI File Pointer placed in correct location")
        )
        lineinfile.add_line_to_file(
            filepath="src/yoshishared.asm",
            line="\t"+"\n\t".join(self.char_proc.yoshi_downspecialstruct_defs),
            inserter=lineinfile.BeforeLast(
                ".*}")
        )

        # dkshared.asm
        # cargo_hold_fix_1
        lineinfile.add_line_to_file(
            filepath="src/dkshared.asm",
            line="\t"+"\n\t".join(self.char_proc.dk_cargo_defs_1),
            inserter=lineinfile.AfterLast(
                r".*beq\s*v0, at, _dkcargo_jump_1.*")
        )
        # cargo_hold_fix_2
        lineinfile.add_line_to_file(
            filepath="src/dkshared.asm",
            line="\t"+"\n\t".join(self.char_proc.dk_cargo_defs_2),
            inserter=lineinfile.AfterLast(
                r".*beq\s*v0, at, _dkcargo_jump_2.*")
        )
        # cargo_item_fix_1
        lineinfile.add_line_to_file(
            filepath="src/dkshared.asm",
            line="\t"+"\n\t".join(self.char_proc.dk_cargo_defs_3),
            inserter=lineinfile.AfterLast(
                r".*beq\s*v0, at, _item_jump_1.*")
        )
        # cargo_item_fix_2
        lineinfile.add_line_to_file(
            filepath="src/dkshared.asm",
            line="\t"+"\n\t".join(self.char_proc.dk_cargo_defs_4),
            inserter=lineinfile.AfterLast(
                r".*beq\s*v0, at, _item_jump_2.*")
        )
        # cargo_item_fix_3
        lineinfile.add_line_to_file(
            filepath="src/dkshared.asm",
            line="\t"+"\n\t".join(self.char_proc.dk_cargo_defs_5),
            inserter=lineinfile.AfterLast(
                r".*beq\s*v0, at, _item_jump_3.*")
        )
        # cargo_item_fix_4
        lineinfile.add_line_to_file(
            filepath="src/dkshared.asm",
            line="\t"+"\n\t".join(self.char_proc.dk_cargo_defs_6),
            inserter=lineinfile.AfterLast(
                r".*beq\s*v0, at, _item_jump_4.*")
        )
        # cargo_item_fix_5
        lineinfile.add_line_to_file(
            filepath="src/dkshared.asm",
            line="\t"+"\n\t".join(self.char_proc.dk_cargo_defs_7),
            inserter=lineinfile.AfterLast(
                r".*beq\s*v0, at, _item_jump_5.*")
        )
        # cargo_item_fix_6
        lineinfile.add_line_to_file(
            filepath="src/dkshared.asm",
            line="\t"+"\n\t".join(self.char_proc.dk_cargo_defs_8),
            inserter=lineinfile.AfterLast(
                r".*beq\s*v0, at, _item_jump_6.*")
        )
        # fully_charged_check_
        lineinfile.add_line_to_file(
            filepath="src/dkshared.asm",
            line="\t"+"\n\t".join(self.char_proc.dk_fully_charged_defs),
            inserter=lineinfile.BeforeFirst(
                r".*beq\s*v0, at, j_0x800EAC64\s*// if JDK, take DK branch.*")
        )
        # kirby_power_check_flash_
        lineinfile.add_line_to_file(
            filepath="src/dkshared.asm",
            line="\t"+"\n\t".join(self.char_proc.dk_kirby_flash_defs),
            inserter=lineinfile.BeforeFirst(
                r".*beq\s*v1, at, j_0x800E9A18\s*// if JDK, take DK branch.*")
        )
        # kirby_power_change_
        lineinfile.add_line_to_file(
            filepath="src/dkshared.asm",
            line="\t"+"\n\t".join(self.char_proc.dk_kirby_power_defs),
            inserter=lineinfile.BeforeFirst(
                r".*beq\s*v0, at, j_0x80161EF0\s*// if JDK, take DK branch.*")
        )
        # giant_punch_fix_1
        lineinfile.add_line_to_file(
            filepath="src/dkshared.asm",
            line="\t"+"\n\t".join(self.char_proc.dk_giant_punch_defs),
            inserter=lineinfile.AfterLast(
                r".*beq\s*v0, at, check_action_giant_punch_.*")
        )
        # cpu_fix_2
        lineinfile.add_line_to_file(
            filepath="src/dkshared.asm",
            line="\t"+"\n\t".join(self.char_proc.dk_cpu_fix_2_defs),
            inserter=lineinfile.AfterLast(
                r".*beq\s*v1, at, _cpu_2.*")
        )

        # Inject patches related to Kirby clones
        kirby_shared.Inject()

        # Training.asm
        nameStrings = []
        dwStrings = []
        dbStrings = []
        idStrings = []
        registerCharacterStrings = []

        for i, cf in enumerate(self.char_folders):
            nameStrings.append(
                f'string_{cf.lower().replace(" ", "_")}:; '
                f'char_Ex{i:02X}:; db "{self.char_proc.character_names[i]}", 0x00'
            )

            dwStrings.append(
                f'dw char_Ex{i:02X}'
            )

            dbStrings.append(
                f'db Character.id.{cf.upper()}'
            )

            idStrings.append(
                f'db id.{cf.upper()}'
            )

            registerCharacterStrings.append(
                f'register_character_id({cf.upper()})'
            )

        lineinfile.add_line_to_file(
            filepath="src/Training.asm",
            line="\t"+"\n\t".join(nameStrings),
            inserter=lineinfile.AfterLast(r".*string_.*0x00")
        )

        lineinfile.add_line_to_file(
            filepath="src/Training.asm",
            line="\t"+"\n\t".join(dwStrings),
            inserter=lineinfile.BeforeLast(r".*dw char_0x0D.*")
        )

        lineinfile.add_line_to_file(
            filepath="src/Training.asm",
            line="\t"+"\n\t".join(dbStrings),
            inserter=lineinfile.BeforeLast(r".*db Character\.id\.METAL.*")
        )

        lineinfile.add_line_to_file(
            filepath="src/Training.asm",
            line="\t"+"\n\t".join(idStrings),
            inserter=lineinfile.AfterLast(r".*// ADD NEW CHARACTERS.*")
        )

        lineinfile.add_line_to_file(
            filepath="src/Training.asm",
            line="\t\t"+"\n\t\t".join(registerCharacterStrings),
            inserter=lineinfile.AfterFirst(r".*// ADD BONUS CHARACTERS HERE.*")
        )

        # SwordTrail.asm
        lineinfile.add_line_to_file(
            filepath="src/SwordTrail.asm",
            line="\t"+"\n\t".join(self.char_proc.sword_trail_add_list),
            inserter=lineinfile.AfterLast(r'^\s*add_sword_trail\(')
        )

        # Costumes.asm
        costume_list = [
            f"db 0x{(self.char_proc.character_skins[n]-1):02X} // {c.upper()}" for n, c in enumerate(self.char_folders)
        ]

        lineinfile.add_line_to_file(
            filepath="src/Costumes.asm",
            line="\t\t"+"\n\t\t".join(costume_list),
            inserter=lineinfile.BeforeFirst(r'^\s*// Polygons')
        )

        # SinglePlayer.asm
        lineinfile.add_line_to_file(
            filepath="src/SinglePlayer.asm",
            line="\t"+"\n\t".join(self.char_proc.singleplayer_additions),
            inserter=lineinfile.AfterLast(r'^\s*add_to_single_player\(.*')
        )

        lineinfile.add_line_to_file(
            filepath="src/SinglePlayer.asm",
            line="\t"+"\n\t".join(self.char_proc.singleplayer_boss_name_defs),
            inserter=lineinfile.AfterLast(r'.*constant DK_CREW\(0x0000D890\).*')
        )

        lineinfile.add_line_to_file(
            filepath="src/SinglePlayer.asm",
            line="\t\t" +
            "\n\t\t".join(
                self.char_proc.singleplayer_name_width_defs["normal"]),
            inserter=lineinfile.BeforeLast(
                r'.*// use normal width otherwise.*')
        )

        # SinglePlayerModes.asm
        lineinfile.add_line_to_file(
            filepath="src/SinglePlayerModes.asm",
            line="\t" +
            "\n\t".join(self.char_proc.singleplayer_remix_match_defs),
            inserter=lineinfile.BeforeLast(
                r".*// Add entry here if a new variant.type.NA character is added UPDATE.*")
        )

        lineinfile.add_line_to_file(
            filepath="src/SinglePlayerModes.asm",
            line="\t\t" +
            "\n\t\t".join(self.char_proc.singleplayer_name_width_defs["team"]),
            inserter=lineinfile.BeforeLast(r'.*b       _adjust_footer_team.*')
        )

        lineinfile.add_line_to_file(
            filepath="src/SinglePlayerModes.asm",
            line="\t\t" +
            "\n\t\t".join(
                self.char_proc.singleplayer_name_width_defs["giant"]),
            inserter=lineinfile.BeforeLast(r'.*b       _done_giant.*')
        )

        add_to_scope("src/SinglePlayerModes.asm", "progress_icon",
                     self.char_proc.character_1p_icon_defs)
        add_to_label_on_empty("src/SinglePlayerModes.asm", "duo_array",
                              self.char_proc.character_1p_duo_parameter_defs)
        add_to_label_on_empty("src/SinglePlayerModes.asm", "team_array",
                              self.char_proc.character_1p_team_parameter_defs)

        # TwelveCharBattle.asm
        lineinfile.add_line_to_file(
            filepath="src/TwelveCharBattle.asm",
            line="\t"+"\n\t".join(self.char_proc.character_12cb_defs),
            inserter=lineinfile.AfterLast(r".*// ADD NEW CHARACTERS HERE.*")
        )

        # TagTeam.asm
        lineinfile.add_line_to_file(
            filepath="src/TagTeam.asm",
            line="\t// Extra characters\n\t" +
            "\n\t".join(self.char_proc.character_tag_team_preloads) +
            "\n\t",
            inserter=lineinfile.BeforeLast(r".*process_preloads().*")
        )

        # CharEnvColor.asm
        for char in self.char_proc.character_cloaking_fix:
            char_fix = self.char_proc.character_cloaking_fix[char]

            lineinfile.add_line_to_file(
                filepath="src/CharEnvColor.asm",
                line="\t"+"\n\t".join(char_fix["struct_defs"])+"\n",
                inserter=lineinfile.BeforeLast(
                    r".*scope custom_display_lists_struct_*")
            )

            lineinfile.add_line_to_file(
                filepath="src/CharEnvColor.asm",
                line="\t\t"+"\n\t\t".join(char_fix["fix_defs"]),
                inserter=lineinfile.BeforeLast(
                    r".*// skip if no fixing necessary*")
            )

            lineinfile.add_line_to_file(
                filepath="src/CharEnvColor.asm",
                line="\t\t"+"\n\t\t".join(char_fix["clear_defs"]),
                inserter=lineinfile.AfterLast(
                    r".*// if JKIRBY, clear JKIRBY's custom display lists*")
            )

            lineinfile.add_line_to_file(
                filepath="src/CharEnvColor.asm",
                line="\t\t"+"\n\t\t".join(char_fix["clear_asm"]),
                inserter=lineinfile.BeforeLast(r".*_clear_kirby:*")
            )

    def _patch_stage_asm(self):
        # Item.asm
        num_items = 0

        with open("src/Item.asm", 'r', encoding='utf-8') as f:
            lines = f.readlines()

            for line in lines:
                m = re.match(r'.*constant NUM_ITEMS\((.*)\)', line)
                if m:
                    num_items = int(m.group(1), 10)

        lineinfile.add_line_to_file(
            filepath="src/Item.asm",
            line=f'\tconstant NUM_ITEMS({num_items}+{self.char_proc.items_added}+{len(self.stage_proc.item_add_list)})',
            regexp=r'.*constant NUM_ITEMS\(.*\)'
        )

        # Stage-registered custom items: splice the add_item(...) calls in after
        # the last one in src/Item.asm (inside scope Item, where the macro and
        # its origin constants are defined).
        if self.stage_proc.item_add_list:
            lineinfile.add_line_to_file(
                filepath="src/Item.asm",
                line="\t" + "\n\t".join(self.stage_proc.item_add_list),
                inserter=lineinfile.AfterLast(r'^\s*add_item\([A-Za-z]')
            )

        # Stages.asm
        # Get NUM_PAGES value
        num_pages = 0
        stages_per_page = 0

        with open("src/Stages.asm", 'r') as f:
            lines = f.readlines()

            for line in lines:
                m = re.match(r'.*constant NUM_PAGES\((.*)\)', line)
                if m:
                    num_pages = int(m.group(1), 16)
                m = re.match(r'.*constant NUM_ROWS\((.*)\)', line)
                if m:
                    num_rows = int(m.group(1), 16)
                m = re.match(r'.*constant NUM_COLUMNS\((.*)\)', line)
                if m:
                    num_columns = int(m.group(1), 16)

            stages_per_page = num_rows * num_columns

        # -1 for the Random stage on the last slot of each page
        pages_needed = len(self.stage_folders) // (stages_per_page - 1) + 1

        if len(self.stage_folders) % (stages_per_page - 1) == 0:
            pages_needed -= 1

        num_pages += pages_needed

        lineinfile.add_line_to_file(
            filepath="src/Stages.asm",
            line=f'\tconstant NUM_PAGES(0x{num_pages:X})',
            regexp=r'.*constant NUM_PAGES\(.*\)'
        )

        max_stage_id = 0

        with open("src/Stages.asm", 'r') as f:
            for line in f:
                m = re.match(r'.*constant MAX_STAGE_ID\((.*)\)', line)
                if m:
                    max_stage_id = int(m.group(1), 16)
                    break

        stage_ids = []
        stage_background_table = []
        stage_zoom_table = []
        add_stage_strings = []
        self.REACHED_RANDOM_STAGE = False
        # We will reach random stage at this index
        self.RANDOM_STAGE_INDEX = 0xDE - max_stage_id - 1

        for i, config in enumerate(self.stage_proc.stage_configs):
            max_stage_id += 1

            # Skip 0xDE since it's the Random stage id
            if max_stage_id == 0xDE:
                max_stage_id += 1
                self.REACHED_RANDOM_STAGE = True

                stage_background_table.append(
                    "db id.PEACHS_CASTLE // RANDOM")

                stage_zoom_table.append(
                    "float32 0.5 // RANDOM")

                add_stage_strings.append(
                    "add_stage("
                    "RANDOM, "
                    '"RANDOM", '
                    "-1, "
                    "-1, "
                    "-1, "
                    "-1, "
                    "OS.FALSE, "
                    "HAZARDS_ON_MOVEMENT_ON, "
                    "OS.FALSE, "
                    "OS.FALSE, "
                    "class.BATTLE, "
                    "-1, -1, -1, -1, -1, 0x05, 0x05, 0x05, default_blue_shell_rate, default_lightning_rate, default_item_rate, default_item_rate, "
                    "SMASH, "
                    "Hazards.type.NONE, "
                    f"{900+len(add_stage_strings)})"
                )

            stage_ids.append(
                f"constant STAGE_{config['id'].upper().replace("/", "_")}(0x{max_stage_id:X})")

            stage_id = max_stage_id + 1

            series = f'{config.get("series", "SMASH")}'

            if self.stage_folders[i] in self.stage_proc.stage_series_textures:
                series = self.stage_folders[i].upper()

            name = config.get("name", config.get("id", ""))

            stage_zoom_table.append(
                f"float32 {config.get("sss_zoom", "0.5")} // {name}")

            stage_background_table.append(
                f"db id.{config.get("training_background", "PEACHS_CASTLE")} // {name}")

            music = [
                config.get('music', {}).get('main', '-1'),
                config.get('music', {}).get('occasional', '-1'),
                config.get('music', {}).get('rare', '-1'),
                config.get('music', {}).get('rare2', '-1')
            ]

            for i, track in enumerate(music):
                if str(track) != '-1':
                    music[i] = f"{{MIDI.id.{track}}}"

            add_stage_strings.append(
                f"add_stage("
                f"{config['id'].upper()}, "
                f'"{name}", '
                f"{music[0]}, "
                f"{music[1]}, "
                f"{music[2]}, "
                f"{music[3]}, "
                f"OS.TRUE, "
                f"HAZARDS_ON_MOVEMENT_ON, "
                f"OS.TRUE, "
                f"OS.TRUE, "
                f"class.BATTLE, "
                f"-1, -1, -1, "
                f'{config.get("base_stage", "-1")}, '
                f'{config.get("variant_type", "-1")}, '
                f"0x05, 0x05, 0x05, default_blue_shell_rate, default_lightning_rate, default_item_rate, default_item_rate, "
                f"{series}, "
                f"Hazards.type.{config.get('hazard_type', 'NONE')}, "
                f"{900+len(add_stage_strings)})"
            )

        lineinfile.add_line_to_file(
            filepath="src/Stages.asm",
            line="\t\t"+"\n\t\t".join(stage_ids),
            inserter=lineinfile.BeforeLast(r'.*constant MAX_STAGE_ID\((.*)\)')
        )

        lineinfile.add_line_to_file(
            filepath="src/Stages.asm",
            line=f'\t\tconstant MAX_STAGE_ID(0x{max_stage_id:X})',
            regexp=r'.*constant MAX_STAGE_ID\(.*\)'
        )

        lineinfile.add_line_to_file(
            filepath="src/Stages.asm",
            line="\t"+"\n\t".join(add_stage_strings),
            inserter=lineinfile.AfterLast(r'.*add_stage\(')
        )

        add_to_scope("src/Stages.asm", "header",
                     self.stage_proc.stage_headers_strings)

        # Assign stages to the menu slots
        # Any extra slots will be filled with the Random stage
        stage_slots = []
        for i, stg in enumerate(self.stage_folders):
            if "/" in stg:
                continue

            if i % (stages_per_page - 1) == 0:
                stage_slots.append(
                    f"// Extra page {i // (stages_per_page - 1) + 1}")

            if i % (stages_per_page - 1) == 0 and i != 0:
                stage_slots.append("db id.RANDOM")

            stage_slots.append(f"db id.STAGE_{stg.upper()}")

        stages_on_last_page = (
            len([s for s in self.stage_folders if "/" not in s]) % (stages_per_page - 1))

        if stages_on_last_page != 0:
            for _ in range((stages_per_page) - stages_on_last_page):
                stage_slots.append("db id.RANDOM")

        lineinfile.add_line_to_file(
            filepath="src/Stages.asm",
            line="\t" +
            "\n\t".join(stage_slots),
            inserter=lineinfile.BeforeLast(r'.*OS.align\(16\)')
        )

        # Add function_table entries for stages
        fun_table_entries = []
        hazards_import_strings = []

        for stg in self.stage_folders:
            # If there's a "hazards.asm" file in the stage folder, use that instead of the default CLONE function
            if os.path.isfile(f"extra_stages/{stg}/hazards.asm"):
                fun_table_entries.append(
                    f"dw Hazards.{stg.upper().replace("/", "_")}_HAZARDS.setup // {stg}")
                hazards_import_strings.append(
                    f'scope {stg.upper().replace("/", "_")}_HAZARDS {{; include "../build/extra_stages/{stg}/hazards.asm"; }}'
                )
            else:
                fun_table_entries.append(
                    f"dw function.CLONE // {stg}"
                )

        if self.REACHED_RANDOM_STAGE:
            fun_table_entries.insert(
                self.RANDOM_STAGE_INDEX, "dw function.CLONE // RANDOM")

        add_to_label("src/Stages.asm", "function_table", fun_table_entries)

        # add hazards imports inside scope Hazards in Hazards.asm
        lineinfile.add_line_to_file(
            filepath="src/Hazards.asm",
            line="\t"+"\n\t".join(hazards_import_strings)+"\n\n",
            inserter=lineinfile.BeforeFirst(r'^} // __HAZARDS__.*')
        )

        # add Character.asm include to Hazards.asm
        lineinfile.add_line_to_file(
            filepath="src/Hazards.asm",
            line="include \"Character.asm\"\n\n",
            inserter=lineinfile.BeforeFirst(r'^scope Hazards.*')
        )

        # generic helper for stage `files:` imports: fill a word with the RAM
        # address of a reqlist-loaded file. Used via resolve_stage_file(id, ptr).
        lineinfile.add_line_to_file(
            filepath="src/Hazards.asm",
            line=(
                "\n"
                "    // @ Description\n"
                "    // Fills the word at a1 with the RAM address of stage-reqlist file a0.\n"
                "    // a0 - file id     a1 - address of the destination word\n"
                "    scope resolve_stage_file_: {\n"
                "        OS.routine_begin(0x20)\n"
                "        sw      a1, 0x0018(sp)\n"
                "        jal     0x800CD698              // lbRelocFindForceStatusBufferFile(a0)\n"
                "        sw      a0, 0x001C(sp)\n"
                "        bnez    v0, _store\n"
                "        lw      a1, 0x0018(sp)\n"
                "        lw      a0, 0x001C(sp)\n"
                "        jal     Render.load_file_      // loads + writes the address into *a1\n"
                "        nop\n"
                "        lw      v0, 0x0000(a1)\n"
                "        _store:\n"
                "        sw      v0, 0x0000(a1)\n"
                "        OS.routine_end(0x20)\n"
                "    }\n"
                "\n"
                "    // @ Description\n"
                "    // resolve_stage_file(FILE_<NAME>, FILE_<NAME>_ptr) - call once in setup.\n"
                "    macro resolve_stage_file(id, ptr) {\n"
                "        lli     a0, {id}\n"
                "        li      a1, {ptr}\n"
                "        jal     Hazards.resolve_stage_file_\n"
                "        nop\n"
                "    }\n"
            ),
            inserter=lineinfile.AfterFirst(r'^scope Hazards\s*\{')
        )

        # Add icon offsets
        stage_icon_offsets = [
            f"dw {self.stage_proc.stage_icon_offsets[i]} // {stg}" for i, stg in enumerate(self.stage_folders)]

        if self.REACHED_RANDOM_STAGE:
            stage_icon_offsets.insert(
                self.RANDOM_STAGE_INDEX, "dw 0x00009BB8 // RANDOM")

        add_to_label("src/Stages.asm", "icon_offset_table", stage_icon_offsets)

        # Add stage zoom table
        add_to_label("src/Stages.asm", "zoom_table", stage_zoom_table)

        # Add stage background table
        add_to_label("src/Stages.asm",
                     "background_table", stage_background_table)

        # Add stage file table
        stage_file_table = [
            f"dw header.STAGE_{stg.upper().replace("/", "_")},\ttype.CLONE" for stg in self.stage_folders]
        if self.REACHED_RANDOM_STAGE:
            stage_file_table.insert(
                self.RANDOM_STAGE_INDEX, "dw 0x0,\ttype.CLONE")

        add_to_label("src/Stages.asm", "stage_file_table", stage_file_table)

        # Camera.asm
        lineinfile.add_line_to_file(
            filepath="src/Camera.asm",
            line="".join(
                self.stage_proc.stages_mushroom_kingdom_camera_strings),
            inserter=lineinfile.BeforeLast(
                r'.*addiu   at, r0, Stages.id.TOADSTURNPIKE')
        )

        # Spawn.asm
        # If reached random stage id, add 0,0,0,0 spawn locations for it
        if self.REACHED_RANDOM_STAGE:
            self.stage_proc.stage_spawn_location_strings["default"].insert(
                self.RANDOM_STAGE_INDEX,
                "\n\t".join([
                    "// RANDOM",
                    "float32 0, 0",
                    "float32 0, 0",
                    "float32 0, 0",
                    "float32 0, 0"
                ])
            )
            self.stage_proc.stage_spawn_location_strings["neutral"].insert(
                self.RANDOM_STAGE_INDEX,
                "\n\t".join([
                    "// RANDOM",
                    "float32 0, 0",
                    "float32 0, 0",
                    "float32 0, 0",
                    "float32 0, 0"
                ])
            )

        add_to_label(
            "src/Spawn.asm",
            "original_table",
            self.stage_proc.stage_spawn_location_strings["default"]
        )

        add_to_label(
            "src/Spawn.asm",
            "neutral_table",
            self.stage_proc.stage_spawn_location_strings["neutral"]
        )

    def _patch_toggle_asm(self):
        # Toggles.asm
        gameplay_toggles = [i.split(".")[0] for i in os.listdir(
            "./extra_toggles/gameplay/") if i.endswith(".asm")]

        gameplay_toggles_strings = []

        # get the label of the last toggle
        LAST_TOGGLE = None

        with open('src/Toggles.asm', 'r') as file:
            lines = file.readlines()

        for i, line in enumerate(lines):
            if re.search(r'evaluate num_gameplay_toggles\(', line):
                # Traverse backwards to find the previous 'entry_.*:'
                for j in range(i - 1, -1, -1):
                    match = re.search(r'(entry_\w+):', lines[j])
                    if match:
                        LAST_TOGGLE = match.group(1)
                        break
                break

        # build toggles strings
        for i, gt in enumerate(gameplay_toggles):
            config = yaml.safe_load(open(
                f"extra_toggles/gameplay/{gt}.yaml"
            ))

            next_toggle = "OS.NULL"

            if i < len(gameplay_toggles)-1:
                next_toggle = f'entry_{gameplay_toggles[i+1]}'

            if config.get("is_bool"):
                gameplay_toggles_strings.append(
                    f'entry_{gt}:;\t\t'
                    f'entry_bool("{config.get("name", gt)}", '
                    f'OS.FALSE, OS.FALSE, OS.FALSE, OS.FALSE, '
                    f'{next_toggle})'
                )

        print(f"Gameplay toggles: {gameplay_toggles}")

        if len(gameplay_toggles) > 0:
            with open('src/Toggles.asm', 'r', encoding='utf-8') as file:
                lines = file.readlines()

            # Find the line with 'evaluate num_gameplay_toggles('
            for i, line in enumerate(lines):
                if re.search(r'evaluate num_gameplay_toggles\(', line):
                    # Traverse backwards to find the previous line ending with 'OS.NULL)'
                    for j in range(i - 1, -1, -1):
                        if lines[j].strip().endswith('OS.NULL)'):
                            # Replace 'OS.NULL)' with '"mylabel"'
                            lines[j] = lines[j].replace('OS.NULL)', f'entry_{
                                gameplay_toggles[0]})')
                            break
                    break

            with open('src/Toggles.asm', 'w', encoding='utf-8') as file:
                file.writelines(lines)

            lineinfile.add_line_to_file(
                filepath="src/Toggles.asm",
                line="\t"+"\n\t".join(gameplay_toggles_strings),
                inserter=lineinfile.BeforeLast(
                    r'.*evaluate num_gameplay_toggles\(')
            )

        # Insert new toggles asm files before Kirby
        lineinfile.add_line_to_file(
            filepath="main.asm",
            line="// Extra toggles\n"+"\n".join(
                [f"include \"extra_toggles/gameplay/{gt}.asm\"" for gt in gameplay_toggles]),
            inserter=lineinfile.BeforeFirst(r"\/\/ KIRBY")
        )

        # Insert extra imports in main asm before characters start (before "// METAL MARIO")
        extra_imports = os.listdir("./extra_imports/")
        extra_imports = [ei for ei in extra_imports if ei.endswith(".asm")]

        lineinfile.add_line_to_file(
            filepath="main.asm",
            line="// Extra imports\n"+"\n".join(
                [f"include \"extra_imports/{ei}\"" for ei in extra_imports])+"\n",
            inserter=lineinfile.BeforeFirst(r"\/\/ METAL MARIO")
        )

        # Add CSS debug menu toggles
        css_toggles = [i for i in os.listdir(
            "./extra_toggles/css/") if i.endswith(".asm")]

        css_toggle_scope_strings = []

        for css_toggle in css_toggles:
            css_toggle_scope_strings.append(
                f"\tscope {css_toggle.split('.')[0]} {{\n"
                f'\t\tinclude "../extra_toggles/css/{css_toggle}"\n'
                f"\t}}"
            )

        lineinfile.add_line_to_file(
            filepath="src/CharacterSelectDebugMenu.asm",
            line="\t// Extra CSS toggles\n" +
            "\n\n".join(css_toggle_scope_strings) +
            "\n\n",
            inserter=lineinfile.BeforeLast(r".*// Add Menu Items.*")
        )

        lineinfile.add_line_to_file(
            filepath="src/CharacterSelectDebugMenu.asm",
            line="\t// Extra CSS toggles\n" +
            "\n".join([f"\tadd_menu_item({csst.split('.')[0]})" for csst in css_toggles]) +
            "\n\n",
            inserter=lineinfile.BeforeLast(r".*// Write Menu Items.*")
        )

        self.audio_proc.patch_toggle_asm()

        # lineinfile patches from custom characters
        for patch in self.char_proc.character_lineinfile_patches:
            itype = patch[2]

            if itype == "BeforeFirst":
                insert = lineinfile.BeforeFirst(patch[3])
            elif itype == "AfterFirst":
                insert = lineinfile.AfterFirst(patch[3])
            elif itype == "BeforeLast":
                insert = lineinfile.BeforeLast(patch[3])
            elif itype == "AfterLast":
                insert = lineinfile.AfterLast(patch[3])
            elif itype != "regexp":
                continue

            if itype == "regexp":
                lineinfile.add_line_to_file(
                    filepath=f"{patch[0]}",
                    line=patch[1],
                    regexp=patch[3]
                )
            else:
                lineinfile.add_line_to_file(
                    filepath=f"{patch[0]}",
                    line=patch[1],
                    inserter=insert
                )


def main(args):
    ca = CharacterAppender(args)
    ca.copy_remix_src()
    ca.overwrite_files()
    ca.prepare_files()
    apply_bio_overflow_patches()
    write_bio_overflow_asm(ca.char_proc.bio_overflow_names)
    ca.inject_files_in_rom()
    ca.edit_src_files()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Appends extra files to Remix and prepare a base rom and source code for building."
    )
    parser.add_argument(
        "--single_character",
        nargs="?",
        help="Build with only a single character added, nothing else. Intended for faster character development."
    )
    parser.add_argument(
        "--single_stage",
        nargs="?",
        help="Build with only a single stage (and its variants) added, no characters. Intended for faster stage development."
    )
    args = parser.parse_args()
    main(args)
