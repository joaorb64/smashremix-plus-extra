import os
import sys
import re
import shutil
import struct
import yaml
from pathlib import Path

from smashremix_extra.constants import (
    SMASHREMIX_PATH as smashremix_path,
    PRIMARY_MOVESETS, SHIELD_POSES, TWELVECB_DEFEAT, SP_DUO_POSES, SP_TEAM_POSES, COMMAND_SIZES, ExtraFile,
)
from smashremix_extra.image_appender import append_image, get_image_data, ImageMode
from smashremix_extra.file_appender import append_file, get_pointer, update_pointer
from smashremix_extra.rom_util import get_attrib_offset
from smashremix_extra.file_manager import FileManager
from smashremix_extra.character.hurtbox import Hurtbox
from smashremix_extra.smashremix.kirbyshared import kirby_shared, KirbyJumpConfig
from smashremix_extra.logger import logger
from smashremix_extra import hex_util, asm_util


class CharacterProcessor:
    """Processes extra character folders and accumulates patch data."""

    def __init__(
        self,
        name_texture_default: str,
        sp_icon_default: str,
        last_sfx_id: int,
        last_remix_sfx_id: int,
        sword_trail_count: int,
        characters_exist: list,
        stage_ids: set = None,
    ):
        self.name_texture_default = name_texture_default
        self.sp_icon_default = sp_icon_default
        self.LAST_SFX_ID = last_sfx_id
        self.LAST_REMIX_SFX_ID = last_remix_sfx_id
        self.SWORD_TRAIL_COUNT = sword_trail_count
        self.characters_exist = characters_exist
        self.stage_ids = stage_ids

        self.bonus_chars = []
        self.character_defs = []
        self.results_screen_defs = []
        self.add_to_css_strings = []
        self.victory_theme_strings = []
        self.singleplayer_additions = []
        self.singleplayer_name_width_defs = {
            "normal": [], "team": [], "giant": []
        }
        self.singleplayer_remix_match_defs = []
        self.character_names = []
        self.character_skins = []
        self.character_series_models = {}
        self.character_series_textures = {}
        self.character_portrait_defs = []
        self.character_1p_icon_defs = []
        self.character_1p_duo_parameter_defs = []
        self.character_1p_team_parameter_defs = []
        self.character_12cb_defs = []
        self.character_tag_team_preloads = []
        self.character_data_screen_defs = []
        self.character_data_screen_order = []
        self.character_lineinfile_patches = []
        self.character_cloaking_fix = {}
        self.data_screen_big_border_defs = []
        self.results_j_win_defs = []
        self.css_dpad_icons = {}
        self.yoshi_jump_defs = []
        self.yoshi_shield_defs = [[] for _ in range(8)]
        self.yoshi_grab1_defs = []
        self.yoshi_grab2_defs = []
        self.yoshi_throw1_defs = []
        self.yoshi_recover_defs = []
        self.yoshi_upspecial_defs = []
        self.yoshi_upspecialstruct_defs = []
        self.yoshi_downspecial_defs = []
        self.yoshi_downspecialstruct_defs = []
        self.dk_cargo_defs_1 = []
        self.dk_cargo_defs_2 = []
        self.dk_cargo_defs_3 = []
        self.dk_cargo_defs_4 = []
        self.dk_cargo_defs_5 = []
        self.dk_cargo_defs_6 = []
        self.dk_cargo_defs_7 = []
        self.dk_cargo_defs_8 = []
        self.dk_fully_charged_defs = []
        self.dk_kirby_flash_defs = []
        self.dk_kirby_power_defs = []
        self.dk_giant_punch_defs = []
        self.dk_cpu_fix_2_defs = []
        self.sound_add_list = []
        self.sword_trail_add_list = []
        self.items_added = 0
        self.midi_priority_overrides = []
        self.midi_bend_range_overrides = []
        self.midi_master_volume_overrides = []

    def _validate_stage_id(self, character_name: str, key: str, stage_id: str, default: str) -> str:
        """Fall back to `default` (logging a warning) if `stage_id` isn't a
        known Stages.id.X name, so a typo'd/removed singleplayer stage
        reference in a character's config.yaml doesn't fail the whole build."""
        if self.stage_ids is not None and stage_id not in self.stage_ids:
            logger.warning(
                f"{character_name}: singleplayer.{key} references unknown stage '{stage_id}'. "
                f"Falling back to '{default}'."
            )
            return default
        return stage_id

    def process(self, character_folder: str) -> None:
        """Process one character folder and accumulate patch data into self."""
        print(f"== {character_folder} ==")
        config = yaml.safe_load(open(
            f"extra_characters/{character_folder}/config.yaml"
        ))
        print(config)

        # Fail if no base character is defined
        if "base_character" not in config.get("definitions", {}):
            print(
                f"ERROR: No base character defined in {character_folder}/config.yaml")
            sys.exit(1)

        # Fail if base character is not a valid character
        if config.get("definitions", {}).get("base_character") not in PRIMARY_MOVESETS:
            print(
                f"ERROR: Base character {config.get('definitions', {}).get('base_character')} is not a valid character")
            sys.exit(1)

        # If no files are defined, set all 4 to [0x0]
        if "files" not in config:
            print(
                "WARNING: No files defined in config.yaml. Setting all files to 0x0")
            config["files"] = [0x0] * 4

        output_path = f"build/extra_characters/{character_folder}/"
        original_path = f"extra_characters/{character_folder}/"

        # Copy all files from the character folder to the output path
        shutil.copytree(
            original_path,
            output_path,
            dirs_exist_ok=True
        )

        filename_to_id = {}

        main_file = FileManager.add_file(
            path=f"{output_path}/main.bin",
            name=f"{character_folder}_main",
            internal_file_table_offset=config["offsets"]["main"][0],
            internal_file_resource_offset=config["offsets"]["main"][1],
            reqlist_path=f"{output_path}/main_reqlist.txt",
            compression_level=2,
            extend_reqlist=config.get("extend_main_reqlist", 0)
        )

        character_file = FileManager.add_file(
            path=f"{output_path}/character.bin",
            name=f"{character_folder}_character",
            internal_file_table_offset=config["offsets"]["character"][0],
            internal_file_resource_offset=config["offsets"]["character"][1],
            reqlist_path=f"{output_path}/character_reqlist.txt" if os.path.exists(
                f"{output_path}/character_reqlist.txt") else None,
            compression_level=2
        )

        req_list_count = 0

        extra_files_str = []
        extra_files_to_add: List[ExtraFile] = []
        extra_files_merged_offsets_str = []

        for index, file in enumerate(config.get("files", [])):
            if isinstance(file, str):
                extra_files_str.append(file)
            elif isinstance(file, list) and len(file) > 0 and isinstance(file[0], list):
                file_id = character_file.id + \
                    1+len(extra_files_to_add)

                # Merge the files in the list
                # Merge files in the list into one
                files_data = []
                files_sizes = []
                reqlist_contents = []
                reqlist_exists = False

                for file_data in file:
                    file_path = f"./{original_path}/{file_data[0]}.bin"

                    with open(file_path, 'rb') as _f:
                        data = bytearray(_f.read())
                        files_sizes.append(len(data))
                        files_data.append(data)

                    # Check for reqlist
                    reqlist_path = f"{original_path}/{file_data[0]}_reqlist.txt"
                    if os.path.exists(reqlist_path):
                        reqlist_exists = True
                        with open(reqlist_path, 'r', encoding='utf-8') as _f:
                            contents = _f.readlines()
                            # Remove "END OF REQLIST" line if present
                            contents = [
                                l for l in contents if not l.startswith("END OF")]
                            reqlist_contents.extend(contents)

                # Merge files and update linked lists
                merged_data = bytearray()

                for i, data in enumerate(files_data):
                    offset_to_add = sum(files_sizes[:i])

                    extra_files_merged_offsets_str.append(
                        f"constant MERGED_FILESTART_{index+6}_{i}(0x{offset_to_add:X})"
                    )

                    # Update first linked list
                    list1_start = int(file[i][1], 16)

                    # Process first linked list
                    current = list1_start

                    while current != 0x3FFFC:
                        list_pos = current

                        next_list_item = get_pointer(data, list_pos, "next")

                        data_part = get_pointer(data, list_pos, "data")
                        if data_part != 0:
                            update_pointer(
                                data, list_pos, data_part + offset_to_add, "data")

                        if next_list_item == 0x3FFFC:
                            if i < len(files_data)-1:
                                # Point to start of next list instead of FFFF
                                next_list_offset = int(
                                    file[i+1][1], 16) + sum(files_sizes[:i+1])
                                update_pointer(
                                    data, list_pos, next_list_offset, "next")
                            break

                        update_pointer(
                            data, list_pos, next_list_item + offset_to_add, "next")
                        current = next_list_item

                    if list1_start != 0x3FFFC:
                        extra_files_merged_offsets_str.append(
                            f"constant MERGED_FILETABLE_OFFSET_{index+6}_{i}(0x{list1_start + offset_to_add:X})")

                    # Update second linked list
                    list2_start = int(file[i][2], 16)

                    # Process second linked list
                    current = list2_start

                    while current != 0x3FFFC:
                        list_pos = current

                        next_list_item = get_pointer(data, list_pos, "next")

                        if next_list_item == 0x3FFFC:
                            if i < len(files_data)-1:
                                # Point to start of next list instead of FFFF
                                next_list_offset = int(
                                    file[i+1][2], 16) + sum(files_sizes[:i+1])
                                update_pointer(
                                    data, list_pos, next_list_offset, "next")
                            break

                        update_pointer(
                            data, list_pos, next_list_item + offset_to_add, "next")
                        current = next_list_item

                    if list2_start != 0x3FFFC:
                        extra_files_merged_offsets_str.append(
                            f"constant MERGED_FILERESOURCE_{index+6}_{i}(0x{list2_start + offset_to_add:X})")

                    merged_data.extend(data)

                # Write merged file
                merged_filename = f"merged_file_{index}"
                with open(f"{output_path}/{merged_filename}.bin", 'wb') as _f:
                    _f.write(merged_data)

                # Write merged reqlist if any existed
                if reqlist_exists:
                    with open(f"{output_path}/{merged_filename}_reqlist.txt", 'w', encoding='utf-8') as _f:
                        _f.writelines(reqlist_contents)
                        _f.write("END OF REQ LIST\n")

                extra_files_str.append(
                    hex(file_id))
                extra_files_to_add.append(
                    ExtraFile(merged_filename, index, file[0][1], file[0][2], file_id))
                filename_to_id[merged_filename] = file_id
            else:
                file_id = character_file.id + \
                    1 + len(extra_files_to_add)
                extra_files_str.append(
                    hex(file_id))
                extra_files_to_add.append(
                    ExtraFile(file[0], index, file[1], file[2], file_id))
                filename_to_id[file[0]] = file_id

        append_files_str = []
        append_files = []

        for index, file in enumerate(config.get("append_files", [])):
            if isinstance(file, str):
                append_files_str.append(file)
            else:
                file_id = character_file.id + 1 + \
                    len(extra_files_to_add)+len(append_files)
                append_files_str.append(
                    hex(file_id))
                append_files.append(
                    ExtraFile(file[0], index, file[1], file[2], file_id))
                filename_to_id[file[0]] = file_id

        shield_pose_int_id = None
        shield_pose_is_external = False

        if not "shield_pose" in config:
            shield_pose_int_id = int(
                SHIELD_POSES[config["definitions"]["base_character"]], 16)
        else:
            if isinstance(config["shield_pose"], str):
                shield_pose_int_id = int(config["shield_pose"], 16)
            else:
                shield_pose_int_id = main_file.id + \
                    1+len(extra_files_to_add)+len(append_files)+1
                shield_pose_is_external = True

        # Compile reqlists
        reqlist_files = [_f for _f in os.listdir(
            f"./{output_path}/") if _f.endswith("reqlist.txt")]
        for reqlist_file in reqlist_files:
            lines = []

            with open(f"./{output_path}/{reqlist_file}", 'r', encoding='utf-8') as reqlist:
                lines = reqlist.readlines()

            with open(f"./{output_path}/{reqlist_file.rsplit(".")[0]}.txt", 'w', encoding='utf-8') as compiled_reqlist:
                for line in lines:
                    line = line.replace("${CHARACTER}",
                                        f"{character_file.id:04X} {character_file.name}")

                    if line.startswith("${"):
                        key = line[2:].strip("}\n")
                        if key in filename_to_id:
                            line = f"{filename_to_id[key]:X}\n"

                    if (line.startswith("${FILE_")):
                        match = re.search(r'\${FILE_(\d)}', line)
                        file_number = int(match.group(1))
                        line = f"{
                            extra_files_str[file_number-6][2:].upper()} FILE_{file_number}\n"

                    line = line.replace("${SHIELD_POSE}",
                                        f"{shield_pose_int_id:04X} SHIELD_POSE")

                    if not line.startswith("END OF") and len(line.strip()) > 0:
                        req_list_count += 1
                        compiled_reqlist.write(line)

        # Add the file id of all append_files to main_reqlist_compiled.txt just before the "END OF..." line
        with open(f"{output_path}/main_reqlist.txt", 'r', encoding='utf-8') as af:
            lines = af.readlines()

        # Find the line that starts with "END OF"
        for i, line in enumerate(lines):
            if line.startswith("END OF"):
                # Insert all append_files game file ids before this line
                lines.insert(
                    i,
                    "\n".join(
                        [f"{af.game_file_id:X} {af.filename}" for af in append_files])+"\n"
                )
                break

        with open(f"{output_path}/main_reqlist.txt", 'w', encoding='utf-8') as af:
            af.writelines(lines)

        # Add extra files to csv
        for ef in extra_files_to_add:
            file_reqlist = ""

            if os.path.exists(f"./{output_path}/{ef.filename}_reqlist.txt"):
                file_reqlist = f"{output_path}/{ef.filename}_reqlist.txt"

            FileManager.add_file(
                path=f"{output_path}/{ef.filename}.bin",
                name=f"{character_folder}_file_{ef.filename}",
                internal_file_table_offset=ef.InternalFileTableOffsetBytes,
                internal_file_resource_offset=ef.InternalFileResourceOffsetBytes,
                reqlist_path=file_reqlist,
                compression_level=1
            )

        # Add append files to csv
        for af in append_files:
            file_reqlist = ""

            if os.path.exists(f"./{output_path}/{af.filename}_reqlist.txt"):
                file_reqlist = f"{output_path}/{af.filename}_reqlist.txt"

            FileManager.add_file(
                path=f"{output_path}/{af.filename}.bin",
                name=f"{character_folder}_file_{af.filename}",
                internal_file_table_offset=af.InternalFileTableOffsetBytes,
                internal_file_resource_offset=af.InternalFileResourceOffsetBytes,
                reqlist_path=file_reqlist,
                compression_level=1
            )

        if shield_pose_is_external:
            FileManager.add_file(
                path=f"{output_path}/shield_pose.bin",
                name=f"{character_folder}_shield_pose",
                internal_file_table_offset=config.get("shield_pose")[
                    0],
                internal_file_resource_offset=config.get("shield_pose")[
                    1],
                compression_level=2
            )

        if os.path.exists(f"{output_path}/animations/"):
            animations = os.listdir(f"{output_path}/animations/")
        else:
            animations = []

        for animation in animations:
            # Read animation file offset
            # Count how many groups of (00000000) we find
            # Each group counts as an offset of 4
            offset = 0

            with open(f"./{output_path}/animations/{animation}", 'rb') as anim_file:
                while True:
                    # Read the first 8 hex digits (4 bytes)
                    chunk = anim_file.read(4)
                    if len(chunk) < 4:
                        break
                    number = int.from_bytes(chunk)
                    if number == 0:
                        offset += 1
                    else:
                        break

            FileManager.add_file(
                path=f"{output_path}/animations/{animation}",
                name=f"{character_folder}_anim_{animation.split('.')[0]}",
                internal_file_table_offset=(offset*4),
                internal_file_resource_offset=int("3FFFC", 16),
                compression_level=0
            )

        print(f"EXTRA FILES STR {extra_files_str}")

        self.character_defs.append(
            f"define_character("
            # id
            f"{character_folder.upper()}, "
            # base character
            f"{config['definitions']['base_character']}, "
            # main file
            f"File.{(character_folder+'_main').upper()}, "
            # primary moveset
            f"{PRIMARY_MOVESETS.get(
                config['definitions']['base_character'])}, "
            # secondary moveset
            f"0, "
            # character file
            f"File.{(character_folder+'_character').upper()}, "
            # shield pose
            f"0x{hex(shield_pose_int_id)[2:].upper()}, "
            # file 6, 7, 8, 9
            f"{', '.join([ef for ef in extra_files_str])}, "
            # attribute offset
            f"0x{get_attrib_offset(
                f'{original_path}/main.bin')}, "
            # add actions
            f"{len(re.findall(
                r".*Character.add_new_action\(",
                open(f"{original_path}/main.asm", 'r', encoding='utf-8').read()))}, "
            # jab3, inhale copy
            f"OS.TRUE, "
            # inhale copy
            f"OS.{config.get("definitions", {}).get("kirby_hat", "FALSE")}, "
            # btt_stage_id
            f"Stages.id.BTT_{config.get("definitions", {}).get("break_the_targets", "STG1")}, "
            # btp_stage_id
            f"Stages.id.BTP_{config.get("definitions", {}).get("board_the_platforms", "POLY")}, "
            # remix_btt_stage_id
            f"Stages.id.BTT_{config.get("definitions", {}).get("break_the_targets", "STG1")}, "
            # remix_btp_stage_id
            f"Stages.id.BTP_{config.get("definitions", {}).get("board_the_platforms", "POLY")}, "
            # sound_type, variant_type
            f"sound_type.U, "
            # variant_type
            f"variant_type.{config.get("definitions", {}).get("variant_type", "SPECIAL")})"
        )

        # Character name
        character_name = character_folder
        if "name" in config:
            character_name = config["name"]
        self.character_names.append(character_name)

        # Skin number
        skin_number = config.get("num_costumes", 8)
        self.character_skins.append(skin_number)

        # Character select name texture
        name_texture = self.name_texture_default

        if os.path.isfile(f'{output_path}/nameplate.png'):
            pixels, w, h = get_image_data(
                f"{output_path}/nameplate.png"
            )
            name_texture = append_image(
                "scripts/0011.bin",
                "scripts/0011.bin",
                pixels,
                w, h,
                ImageMode.IA8
            )
            name_texture = f"0x{name_texture:08X} + 0x10"

        # Generate model req file
        print(f"To write on MODEL_REQ file: {character_file.id:4X}")
        char_hex_id_bytes = bytes.fromhex(f"{character_file.id:4X}")

        # Create the binary file and write the hex digits
        with open(f'{output_path}/MODEL_REQ.req', 'wb') as binary_file:
            for _ in range(req_list_count):
                binary_file.write(char_hex_id_bytes)

        character_sound_add_list = {}

        # Choose CSS Pose (This grabs the digit specified by "select_pose" in the character's config.yaml)
        select_pose = config.get("definitions", {}).get("select_pose", 1)
        select_pose_string = f"0x0001000{select_pose}"

        # Compile any sounds/<name>.wav -> <name>.aifc first. Target rate from
        # sounds_special[<id>].sample_rate when that id maps to the file name
        # (config['sounds']: id -> name), else 16000 like add_sound's default.
        from smashremix_extra.audio import vadpcm
        _snd_map = config.get("sounds", {}) or {}
        _snd_special = config.get("sounds_special", {}) or {}
        _name_rate = {
            nm: (_snd_special.get(sid) or {}).get("sample_rate", 16000)
            for sid, nm in _snd_map.items()
        }
        vadpcm.convert_dir(
            f"./{output_path}/sounds",
            rate_for=lambda n: _name_rate.get(n, 16000))

        # Get sounds to add
        if os.path.exists(f"./{output_path}/sounds"):
            for s in os.listdir(f"./{output_path}/sounds"):
                # .wav sources were compiled to .aifc above; skip the originals.
                if not s.lower().endswith(".aifc"):
                    continue
                sample_rate = 16000
                type = "VOICE"
                reverb = 0
                length = -1

                sound_list = config.get("sounds", {})

                # Check for custom sound settings
                for sid in sound_list:
                    if sound_list.get(f"{sid}") != s.rsplit('.', 1)[0]:
                        continue

                    sound_settings = config.get("sounds_special", {}).get(sid)
                    if sound_settings:
                        sample_rate = sound_settings.get("sample_rate", 16000)
                        type = sound_settings.get("fgm_type", "VOICE")
                        reverb = sound_settings.get("reverb", 0)
                        length = sound_settings.get("length", -1)

                # If announcer sound, set reverb
                if config.get("announcer_fgm"):
                    if sound_list.get(config.get("announcer_fgm")) == s.rsplit('.', 1)[0]:
                        reverb = 40

                character_sound_add_list[s.rsplit('.', 1)[0]] = f"{
                    (self.LAST_SFX_ID + 1):04X}"
                self.LAST_SFX_ID += 1
                self.sound_add_list.append(
                    f"add_sound(../{output_path}sounds/{
                        s.rsplit('.', 1)[0]}, "
                    f"SAMPLE_RATE_{sample_rate}, FGM_TYPE_{type}, {reverb}, {length})"
                )

        print(f"SOUND_ADD_LIST(character): {character_sound_add_list}")

        announcer_fgm = "FGM.announcer.names.BONUS_CHARACTER"

        if config.get("announcer_fgm"):
            sound_name = config.get("sounds").get(
                config.get("announcer_fgm"))
            announcer_fgm = f"0x{
                character_sound_add_list.get(sound_name)}"

        # Calculate 1P name delay if announcer FGM is found
        sp_config = config.get("singleplayer", {})
        name_delay_sp = "name_delay.DRAGONKING"

        if sp_config.get("name_delay"):
            name_delay_sp = f"0x{sp_config.get("name_delay"):08X}"
        elif config.get("announcer_fgm"):
            announcer = config.get("sounds").get(
                config.get("announcer_fgm"))
            with open(f"./{output_path}/sounds/{announcer}.aifc", "rb") as aifc:
                aifc.seek(4)
                aifc_length = int.from_bytes(aifc.read(4), "big")
                aifc_length = aifc_length / 375
                aifc_length = round(aifc_length * 0.65)

                name_delay_sp = f"0x{aifc_length:08X}"

        # Check for 1P name texture and use if found
        name_texture_sp = "name_texture.MARIO"

        if os.path.exists(f"{output_path}/nameplate_singleplayer.png"):
            pixels, w, h = get_image_data(
                f"{output_path}/nameplate_singleplayer.png"
            )
            name_texture_sp = append_image(
                "scripts/000C.bin",
                "scripts/000C.bin",
                pixels,
                w, h,
                ImageMode.I8
            )
            name_texture_sp = f"0x{name_texture_sp:08X}"

        self.singleplayer_additions.append(
            f'add_to_single_player(Character.id.{character_folder.upper()}, {name_texture_sp}, {name_delay_sp})')

        # Use alternate width for character's 1P name texture if defined
        alt_name_width = sp_config.get("alt_name_width", None)
        alt_name_width_team = sp_config.get(
            "alt_name_width_team", alt_name_width)
        alt_name_width_giant = sp_config.get(
            "alt_name_width_giant", alt_name_width)

        if alt_name_width:
            self.singleplayer_name_width_defs["normal"].append(
                f"lli     t6, Character.id.{character_folder.upper()}\n\t\t"
                f"beql    t0, t6, _alt_width                // use alt width if {character_name}\n\t\t"
                f"lli     t6, 0x{alt_name_width:04X}                        // t6 = width of \"{character_name}\""
            )

        if alt_name_width or alt_name_width_team:
            self.singleplayer_name_width_defs["team"].append(
                f"lli     t6, {name_texture_sp} + 0x10\n\t\t"
                f"beql    t8, t6, _set_alt_width_team // if {character_name}, use alternate width\n\t\t"
                f"lli     t6, 0x{alt_name_width_team:04X}                  // t6 = width of \"{character_name}\""
            )

        if alt_name_width or alt_name_width_giant:
            self.singleplayer_name_width_defs["giant"].append(
                f"lli     t6, {name_texture_sp} + 0x10\n\t\t"
                f"beql    t8, t6, _set_alt_width_giant // if {character_name}, use alternate width\n\t\t"
                f"lli     t6, 0x{alt_name_width_giant:04X}                  // t6 = width of \"{character_name}\""
            )

        # Check for 1P icon and use if found
        icon_offset = self.sp_icon_default

        if os.path.isfile(f"{output_path}/1p_icon.png"):
            pixels, w, h = get_image_data(
                f"{output_path}/1p_icon.png"
            )
            icon_offset = append_image(
                "scripts/000B.bin",
                "scripts/000B.bin",
                pixels,
                w, h,
                ImageMode.RGBA5551
            )
            icon_offset = f"0x{icon_offset:X} + 0x10"

        self.character_1p_icon_defs.append(
            f"constant {character_folder.upper()}({icon_offset})")

        singleplayer_icon = f"progress_icon.{character_folder.upper()}"

        # Remix 1P Character Battle versus parameters
        if config.get("definitions", {}).get("variant_type", "SPECIAL") == "NA":

            flags = sp_config.get("flags", 0)

            stage1 = self._validate_stage_id(
                character_name, "stage1", sp_config.get("stage1", "DREAM_LAND"), "DREAM_LAND")
            stage2 = self._validate_stage_id(
                character_name, "stage2", sp_config.get("stage2", "DREAM_LAND"), "DREAM_LAND")
            stage3 = self._validate_stage_id(
                character_name, "stage3", sp_config.get("stage3", "DREAM_LAND"), "DREAM_LAND")

            scale = sp_config.get("scale", "6F80").zfill(8)

            self.singleplayer_remix_match_defs.extend([
                f"// {character_name} match settings",
                f"{character_folder.lower()}_match_setting:",
                f"dw  0x{flags} // flag",
                f"db  Character.id.{character_folder.upper()} // Character ID",
                f"db  Stages.id.{stage1} // Stage Option 1",
                f"db  Stages.id.{stage2} // Stage Option 2",
                f"db  Stages.id.{stage3} // Stage Option 3",
                f"dw  {name_texture_sp} + 0x10 // name texture",
                f"dw  {announcer_fgm} // Announcer Call",
                f"dw  0x{scale} // Model Scale",
                f"dw  progress_icon.{character_folder.upper()} // Progress Icon\n",
            ])

        # Remix 1P Duo menu parameters
        duo_config = sp_config.get("duo", {})

        anim = duo_config.get(
            "anim",
            SP_DUO_POSES.get(config['definitions']['base_character'])
        )
        moveset = duo_config.get("moveset", "duo_moveset")
        flags = duo_config.get("flags", 0)

        self.character_1p_duo_parameter_defs.append(
            f"add_duo_parameters({anim}, {moveset}, {flags}) // {character_folder.upper()}")

        # Remix 1P Team menu parameters
        team_config = sp_config.get("team", {})

        anim = team_config.get(
            "anim",
            SP_TEAM_POSES.get(config['definitions']['base_character'])
        )
        moveset = team_config.get("moveset", "team_moveset")
        flags = team_config.get("flags", 0)

        self.character_1p_team_parameter_defs.append(
            f"add_team_parameters({anim}, {moveset}, {flags}) // {character_folder.upper()}")

        # Get series to use for character
        series_css = config.get("definitions", {}).get(
            "series_logo_css", "SMASH")
        series_model = config.get("definitions", {}).get(
            "series_logo_model", "SMASH")

        # Check for CSS series logo image and use if found
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
            series_texture = series_texture + 16

            self.character_series_textures[f"{character_folder}"] = {
                "offset": f"0x{series_texture:X}",
                "x": "0x40400000",
                "y": "0x41980000"
            }

            series_css = character_folder.upper()

        portrait_texture = "portrait_offsets.MARIO"

        # If portrait override character exists, use their portrait
        if config.get("dpad_original", "") in self.characters_exist and os.path.isfile(
                f"extra_characters/{config.get("dpad_original", "")}/portrait.png"):
            portrait_texture = f"portrait_offsets.{config.get("dpad_original", "").upper()}"
        # Otherwise, check for portrait image and use if found
        elif os.path.isfile(f"{output_path}/portrait.png"):
            if os.path.getsize("scripts/0A05.bin") + (0x860 * 2) < 0x3FFFC:
                pixels, w, h = get_image_data(
                    f"{output_path}/portrait.png"
                )
                portrait_texture = append_image(
                    "scripts/0A05.bin",
                    "scripts/0A05.bin",
                    pixels,
                    w, h,
                    ImageMode.RGBA5551
                )

                self.character_portrait_defs.append(
                    f"constant {character_folder.upper()}(0x{portrait_texture:08X} + 0x10)")
                portrait_texture = f"portrait_offsets.{character_folder.upper()}"

                # Check for flash portrait
                if os.path.isfile(f"{output_path}/portrait_flash.png"):
                    pixels, w, h = get_image_data(
                        f"{output_path}/portrait_flash.png"
                    )
                    append_image(
                        "scripts/0A05.bin",
                        "scripts/0A05.bin",
                        pixels,
                        w, h,
                        ImageMode.RGBA5551
                    )
                else:
                    # If not found, append the regular portrait as the flash portrait
                    append_image(
                        "scripts/0A05.bin",
                        "scripts/0A05.bin",
                        pixels,
                        w, h,
                        ImageMode.RGBA5551
                    )

        self.add_to_css_strings.append(
            f"add_to_css(Character.id.{character_folder.upper()}, "
            f"{announcer_fgm}, "
            f"1.50, "
            f"{select_pose_string}, "
            f"{series_css}, "
            f"{name_texture}, "
            f"{portrait_texture}, "
            f"BOOKEND_BONUS_PORTRAIT)"
        )

        # Add character to bonus slot if not variant or variant not existing
        if not config.get("dpad_original", "") in self.characters_exist:
            self.bonus_chars.append(character_folder)

        # Check for D-Pad/Variant icon and add if found
        if os.path.isfile(f"{output_path}/dpad.png"):
            pixels, w, h = get_image_data(
                f"{output_path}/dpad.png"
            )
            variant_icon = append_image(
                "scripts/0A06.bin",
                "scripts/0A06.bin",
                pixels,
                w, h,
                ImageMode.RGBA5551
            )

            self.css_dpad_icons[character_folder.upper()] = variant_icon

        # Generate results screen definition
        victory_theme = "0x0B"

        if os.path.exists(f"{output_path}/victory_theme.bin"):
            self.victory_theme_strings.append(
                f"insert_external_midi({character_folder}_VICTORY, "
                f"../{output_path}victory_theme, "
                f"OS.FALSE, "
                f"OS.FALSE, "
                f"OS.FALSE, "
                f"OS.FALSE, "
                f"-1, "
                f"-1, "
                f"{900+len(self.victory_theme_strings)})"
            )

            victory_theme = f'{{MIDI.id.{character_folder}_VICTORY}}'

            if config.get("victory_theme", {}).get("priority_overrides"):
                for override in config.get("victory_theme", {}).get("priority_overrides"):
                    self.midi_priority_overrides.append(
                        f"add_priority_override("
                        f"{{MIDI.id.{character_folder}_VICTORY}}, "
                        f"{override[0]}, "
                        f"{override[1]})"
                    )

            if config.get("victory_theme", {}).get("bend_range_overrides"):
                for override in config.get("victory_theme", {}).get("bend_range_overrides"):
                    self.midi_bend_range_overrides.append(
                        f"add_bend_range_override("
                        f"{{MIDI.id.{character_folder}_VICTORY}}, "
                        f"{override[0]}, "
                        f"{override[1]})"
                    )

            if config.get("victory_theme", {}).get("master_volume_override"):
                self.midi_master_volume_overrides.append(
                    f"add_master_volume_override("
                    f"{{MIDI.id.{character_folder}_VICTORY}}, "
                    f"{config.get("victory_theme", {}).get("master_volume_override")})"
                )

        if config.get("results", {}).get("j_win_text", False) is True:
            self.results_j_win_defs.append(
                f"lli     t6, Character.id.{character_folder.upper()}      // t6 = {character_folder.upper()}"
                f"\n\t\tbeql    t5, t6, _get_lx             // if {character_folder.upper()}, set to WIN!"
                f"\n\t\taddiu   a0, a0, 0x000C              // a0 = offset to \"WIN!\""
            )

        # Check for 3D results/data series logo file and use if found
        if os.path.exists(f"{output_path}/series_logo.bin"):
            with open(f"{output_path}/series_logo.bin", 'rb') as logo_file:
                logo = bytearray(logo_file.read())

            # Seems like Ness logo data always begins here once imported through GEE
            logo_offset = 0x5C58

            # Internal file table offset after GEE logo import (offset to first pointer)
            pointer_offset = 0x624

            while pointer_offset < logo_offset:
                next_pointer = get_pointer(logo, pointer_offset, "next")

                if next_pointer > logo_offset:
                    # got first pointer in character's logo data
                    pointer_offset = next_pointer - logo_offset
                    break

                pointer_offset = next_pointer

            with open(f"{output_path}/series_logo.bin", 'wb') as logo_file:
                logo_file.write(logo[logo_offset:])

            append_file(
                f"{output_path}/series_logo.bin", logo_offset, pointer_offset,
                "scripts/0023.bin", 0x4,
                "scripts/0023.bin"
            )

            logo_length = os.path.getsize("scripts/0023.bin")

            # Get series logo offsets
            self.character_series_models[f"{character_folder}"] = {
                "offset": f"0x{(logo_length - 0x168):0X}",
                "zoom":   f"0x{(logo_length - 0x60):0X}",
                "color":  f"0x{(logo_length - 0x8):0X}"
            }

            series_model = character_folder.upper()

        results_name = config.get("results", {}).get(
            "name", character_name).upper()
        name_len = len(results_name)
        name_len_adjusted = max(0, name_len - 3)

        wins_lx_mult = 6.5
        str_lx_mult = 9.5
        str_scale_mult = 0.055

        wins_max_lx = 185
        wins_lx = 160
        str_lx = 50

        if config.get("results", {}).get("j_win_text", False) is True:
            wins_max_lx += 25
            wins_lx += 15
            str_lx += 15

            str_scale_mult -= 0.0085

        wins_lx += (name_len_adjusted * wins_lx_mult)
        wins_lx = int(min(wins_max_lx, wins_lx))

        str_lx -= (name_len_adjusted * str_lx_mult)
        str_lx = int(max(20, str_lx))

        str_scale = round(
            min(1.0, 1.05 - name_len_adjusted * str_scale_mult), 2)

        # Override generated values with config if found
        wins_lx = config.get("results", {}).get("wins_x", wins_lx)
        str_lx = config.get("results", {}).get("name_x", str_lx)
        str_scale = config.get("results", {}).get("name_scale", str_scale)
        victory_theme = config.get("results", {}).get("win_bgm", victory_theme)

        self.results_screen_defs.append(
            "add_to_results_screen("
            f"Character.id.{character_folder.upper()}, "
            f"{announcer_fgm}, "
            f"{series_model}, "
            f"Character.id.{config['definitions']['base_character']}, "
            f"{wins_lx}, "
            f"{results_name}, "
            f"{str_lx}, "
            f"{str_scale}, "
            f"{victory_theme}"
            ")"
        )

        # Count any new items being added
        item_add_pattern = re.compile(r"Item\.add_item\(")

        for file in Path(original_path).rglob("*.asm"):
            with open(file, encoding="utf-8") as check_file:
                for line in check_file:
                    # remove any comments
                    code = line.split("//", 1)[0]
                    self.items_added += len(
                        item_add_pattern.findall(code))

        # Data screen additions
        bio_texture = "0x8000FF08"
        name_texture = "0x80010128"
        works_texture = "0x80009B48"

        usp_texture = "offset.crash_body_slam"
        nsp_texture = "offset.spin"
        dsp_texture = "offset.diggin_it"

        name_x = config.get("data_screen", {}).get("name_x", 33)
        name_y = config.get("data_screen", {}).get("name_y", 50)

        use_existing_special_actions = config.get(
            "data_screen", {}).get(
            "use_existing_special_actions", 1)

        special_char = config.get(
            "data_screen", {}).get(
            "special_char", config['definitions']['base_character'])

        if use_existing_special_actions == 0:
            special_char = 0

        use_existing_jab_actions = config.get(
            "data_screen", {}).get(
            "use_existing_jab_actions", 1)

        jab_char = config.get(
            "data_screen", {}).get(
            "jab_char", config['definitions']['base_character'])

        if use_existing_jab_actions == 0:
            jab_char = -1

        # Fix for DK clones as special_action_pointers and jab_action_pointers
        # have their constants for Donkey Kong listed as only 'DK', so 'DONKEY' fails
        if special_char == "DONKEY":
            special_char = "DK"
        if jab_char == "DONKEY":
            jab_char = "DK"

        # C.F clones will also use this workaround ('FALCON' instead of 'CAPTAIN')
        if special_char == "CAPTAIN":
            special_char = "FALCON"
        if jab_char == "CAPTAIN":
            jab_char = "FALCON"

        # Check for Data screen textures (bio, name, works, specials)
        if os.path.exists(f"{output_path}/datascreen/bio.png"):
            # Bios are stored as three stacked I4 strips (51 + 51 + 13 rows);
            # the biography table pointer + 0x30 must land past exactly three
            # segment nodes, so the source image must be 160x115.
            pixels, w, h = get_image_data(
                f"{output_path}/datascreen/bio.png", 160, 115
            )
            bio_texture = append_image(
                "scripts/10F5.bin",
                "scripts/10F5.bin",
                pixels,
                w, h,
                ImageMode.I4,
            )
            bio_texture += 0x80000000
            bio_texture = f"0x{bio_texture:08X}"

        if os.path.exists(f"{output_path}/datascreen/name.png"):
            pixels, w, h = get_image_data(
                f"{output_path}/datascreen/name.png"
            )
            name_texture = append_image(
                "scripts/10F6.bin",
                "scripts/10F6.bin",
                pixels,
                w, h,
                ImageMode.I4,
            )
            name_texture += 0x80000000
            name_texture = f"0x{name_texture:08X}"

        if os.path.exists(f"{output_path}/datascreen/works.png"):
            pixels, w, h = get_image_data(
                f"{output_path}/datascreen/works.png"
            )
            works_texture = append_image(
                "scripts/10F6.bin",
                "scripts/10F6.bin",
                pixels,
                w, h,
                ImageMode.I4,
            )
            works_texture += 0x80000000
            works_texture = f"0x{works_texture:08X}"

        if os.path.exists(f"{output_path}/datascreen/special_u.png"):
            pixels, w, h = get_image_data(
                f"{output_path}/datascreen/special_u.png"
            )
            usp_texture = append_image(
                "scripts/10F6.bin",
                "scripts/10F6.bin",
                pixels,
                w, h,
                ImageMode.I4,
            )
            usp_texture += 0x80000000
            usp_texture = f"0x{usp_texture:08X}"

        if os.path.exists(f"{output_path}/datascreen/special_n.png"):
            pixels, w, h = get_image_data(
                f"{output_path}/datascreen/special_n.png"
            )
            nsp_texture = append_image(
                "scripts/10F6.bin",
                "scripts/10F6.bin",
                pixels,
                w, h,
                ImageMode.I4,
            )
            nsp_texture += 0x80000000
            nsp_texture = f"0x{nsp_texture:08X}"

        if os.path.exists(f"{output_path}/datascreen/special_d.png"):
            pixels, w, h = get_image_data(
                f"{output_path}/datascreen/special_d.png"
            )
            dsp_texture = append_image(
                "scripts/10F6.bin",
                "scripts/10F6.bin",
                pixels,
                w, h,
                ImageMode.I4,
            )
            dsp_texture += 0x80000000
            dsp_texture = f"0x{dsp_texture:08X}"

        add_to_data_string = (
            f"add_char_to_data_screen("
            f"{character_folder.upper()}, "
            f"{bio_texture}, "
            f"0x00000000, "
            f"0x00000000, "
            f"{name_x}, "
            f"{name_y}, "
            f"{name_texture}, "
            f"{works_texture}, "
            f"{nsp_texture}, "
            f"{dsp_texture}, "
            f"{usp_texture}, "
            f"{use_existing_special_actions}, "
            f"{special_char}, "
            f"{use_existing_jab_actions}, "
            f"{jab_char})"
        )

        set_action_strings = [""]
        for action in config.get("data_screen", {}).get("actions", []):
            if len(action) < 4:
                action.append("0x00000000")

            if isinstance(action[2], int):
                action[2] = f"0x{action[2]:0X}"

            set_action_strings.append(
                f"set_action({", ".join(action)})")

        if config.get("data_screen", {}):
            self.character_data_screen_defs.append(
                add_to_data_string + "\n\t\t".join(set_action_strings)
            )

            self.character_data_screen_order.append(
                f"set_char_order({character_folder.upper()})"
            )

        if config.get("data_screen", {}).get("name_big_border") == True:
            self.data_screen_big_border_defs.append(
                f"addiu   at, r0, Character.id.{character_folder.upper()}  // at = Character ID"
                f"\n\t\tbeq     v0, at, _large_border       // branch to use large border"
            )

        # 12 Character Battle defeat parameters
        anim = config.get("12cb", {}).get(
            "anim",
            TWELVECB_DEFEAT.get(config['definitions']['base_character'])
        )
        moveset = config.get("12cb", {}).get("moveset", "defeated_moveset")
        flags = config.get("12cb", {}).get("flags", 0)
        self.character_12cb_defs.append(
            f"add_defeat_parameters({anim}, {moveset}, {flags}) // {character_folder.upper()}"
        )

        # Add Tag Team preloads
        self.character_tag_team_preloads.append(
            f"add_preload(Character.id.{character_folder.upper()}, "
            f"{PRIMARY_MOVESETS.get(config['definitions']['base_character'])}) "
            f"// {config['definitions']['base_character']} move set"
        )

        for preload in config.get("tag_team_preloads", []):
            file_id = preload

            if preload in filename_to_id:
                file_id = f"0x{filename_to_id[preload]:X}"

            self.character_tag_team_preloads.append(
                f"add_preload(Character.id.{character_folder.upper()}, "
                f"{file_id}) "
                f"// {preload}"
            )

        if config.get("yoshi_jump") == True:
            self.yoshi_jump_defs.append(
                f"\taddiu   a0, r0, Character.id.{character_folder.upper()}     // {character_folder.upper()} ID"
                f"\n\t\tbeq     a0, v0, _yoshi_dj_1"
            )

        if config.get("yoshi_shield") == True:
            _shield_regs = ["t7", "t9", "t6", "t8", "t1", "t7", "v1", "t8"]
            for i, reg in enumerate(_shield_regs):
                self.yoshi_shield_defs[i].append(
                    f"\taddiu   at, r0, Character.id.{character_folder.upper()}             // {character_folder.upper()} ID"
                    f"\n\t\tbeq     {reg}, at, _yoshi_shield_{i+1}"
                )

        if config.get("yoshi_grab") == True:
            self.yoshi_grab1_defs.append(
                f"\taddiu   at, r0, Character.id.{character_folder.upper()}             // {character_folder.upper()} ID"
                f"\n\t\tbeq     at, v0, _yoshi_grab_1"
            )
            self.yoshi_grab2_defs.append(
                f"\taddiu   at, r0, Character.id.{character_folder.upper()}             // {character_folder.upper()} ID"
                f"\n\t\tbeq     at, v0, _yoshi_grab_2"
            )
            self.yoshi_throw1_defs.append(
                f"\taddiu   at, r0, Character.id.{character_folder.upper()}             // {character_folder.upper()} ID"
                f"\n\t\tbeq     at, v0, _yoshi_throw_1"
            )

        if config.get("yoshi_recover") == True:
            self.yoshi_recover_defs.append(
                f"\taddiu   at, r0, Character.id.{character_folder.upper()}             // {character_folder.upper()} ID"
                f"\n\t\tbeq     at, v0, _yoshi_recover_1"
            )

        if config.get("yoshi_upspecial") == True:
            self.yoshi_upspecial_defs.append(
                f"\tbeq     t1, t2, _end"
                f"\n\t\taddiu   t1, r0, Character.id.{character_folder.upper()}             // {character_folder.upper()} ID"
                f"\n\t\tli      a1, upspecial_struct_{character_folder.upper()}     // {character_folder.upper()} File Pointer placed in correct location"
            )
            self.yoshi_upspecialstruct_defs.append(
                f"\n\tOS.align(16)"
                f"\n\tupspecial_struct_{character_folder.upper()}:"
                f"\n\tdw 0x00000000"
                f"\n\tdw 0x00000005"
                f"\n\tdw Character.{character_folder.upper()}_file_1_ptr"
                f"\n\tOS.copy_segment(0x103D2C, 0x40)\n "
            )

        if config.get("yoshi_downspecial") == True:
            self.yoshi_downspecial_defs.append(
                f"\tbeq     t1, t2, _end"
                f"\n\t\taddiu   t1, r0, Character.id.{character_folder.upper()}             // {character_folder.upper()} ID"
                f"\n\t\tli      a1, downspecial_struct_{character_folder.upper()}    // {character_folder.upper()}  File Pointer placed in correct location"
            )
            self.yoshi_downspecialstruct_defs.append(
                f"\n\tOS.align(16)"
                f"\n\tdownspecial_struct_{character_folder.upper()}:"
                f"\n\tdw 0x00000000"
                f"\n\tdw 0x00000006"
                f"\n\tdw Character.{character_folder.upper()}_file_1_ptr"
                f"\n\tOS.copy_segment(0x103D6C, 0x40)\n "
            )

        if config.get("dk_cargo"):
            self.dk_cargo_defs_1.append(
                f"\taddiu   at, r0, Character.id.{character_folder.upper()} // {character_folder.upper()} ID"
                f"\n\t\tbeq v0, at, _dkcargo_jump_1"
            )
            self.dk_cargo_defs_2.append(
                f"\taddiu   at, r0, Character.id.{character_folder.upper()} // {character_folder.upper()} ID"
                f"\n\t\tbeq v0, at, _dkcargo_jump_2"
            )
            self.dk_cargo_defs_3.append(
                f"\taddiu   at, r0, Character.id.{character_folder.upper()} // {character_folder.upper()} ID"
                f"\n\t\tbeq     v0, at, _item_jump_1"
            )
            self.dk_cargo_defs_4.append(
                f"\taddiu   at, r0, Character.id.{character_folder.upper()} // {character_folder.upper()} ID"
                f"\n\t\tbeq     v0, at, _item_jump_2"
            )
            self.dk_cargo_defs_5.append(
                f"\taddiu   at, r0, Character.id.{character_folder.upper()} // {character_folder.upper()} ID"
                f"\n\t\tbeq     v0, at, _item_jump_3"
            )
            self.dk_cargo_defs_6.append(
                f"\taddiu   at, r0, Character.id.{character_folder.upper()} // {character_folder.upper()} ID"
                f"\n\t\tbeq     v0, at, _item_jump_4"
            )
            self.dk_cargo_defs_7.append(
                f"\taddiu   at, r0, Character.id.{character_folder.upper()} // {character_folder.upper()} ID"
                f"\n\t\tbeq     v0, at, _item_jump_5"
            )
            self.dk_cargo_defs_8.append(
                f"\taddiu   at, r0, Character.id.{character_folder.upper()} // {character_folder.upper()} ID"
                f"\n\t\tbeq     v0, at, _item_jump_6"
            )

            # Extends dkshared.asm's "is this fighter a DK clone" ID checks
            # (only recognize Character.id.JDK) to also recognize this
            # character: fully-charged Giant Punch effect, DK-powered Kirby
            # copy ability flash/change, and Giant Punch/cargo action ID
            # checks used while CPU-controlled.
            self.dk_fully_charged_defs.append(
                f"\tbeq     v0, at, j_0x800EAC64        // original line 1, modified to use jump"
                f"\n\t\tlli     at, Character.id.{character_folder.upper()}        // at = {character_folder.upper()}"
            )
            self.dk_kirby_flash_defs.append(
                f"\tbeq     v1, at, j_0x800E9A18        // original line 1, modified to use jump"
                f"\n\t\tlli     at, Character.id.{character_folder.upper()}        // at = {character_folder.upper()}"
            )
            self.dk_kirby_power_defs.append(
                f"\tbeq     v0, at, j_0x80161EF0        // original line 1, modified to use jump"
                f"\n\t\tlli     at, Character.id.{character_folder.upper()}        // at = {character_folder.upper()}"
            )
            self.dk_giant_punch_defs.append(
                f"\taddiu   at, r0, Character.id.{character_folder.upper()} // {character_folder.upper()} ID"
                f"\n\t\tbeq     v0, at, check_action_giant_punch_"
            )
            self.dk_cpu_fix_2_defs.append(
                f"\taddiu   at, r0, Character.id.{character_folder.upper()} // {character_folder.upper()} ID"
                f"\n\t\tbeq     v1, at, _cpu_2"
            )

        if config.get("kirby_jumps"):
            kirby_shared.configs.append(KirbyJumpConfig(
                character_id=character_folder.upper(),
                height_multiplier_3=float(config.get(
                    "kirby_jumps").get("height_multiplier_3", 100.0)),
                height_multiplier_4=float(config.get(
                    "kirby_jumps").get("height_multiplier_4", 100.0)),
                height_multiplier_5=float(config.get(
                    "kirby_jumps").get("height_multiplier_5", 100.0)),
                height_multiplier_6=float(config.get(
                    "kirby_jumps").get("height_multiplier_6", 100.0)),
                jump_decay=float(config.get(
                    "kirby_jumps").get("jump_decay", 80))
            ))

        # Check for display list fixes from characters (primarily for cloaking device issues)
        dpl_config = config.get("cloaking_fix", {})
        fix_list = dpl_config.get("parts", {})
        parts = {obj: {} for obj in fix_list}
        for obj in fix_list:
            for poly in ["hipoly", "lopoly"]:
                poly_config = fix_list.get(obj).get(poly, {})
                if not poly_config:
                    continue

                offsets = poly_config.get("cmd_offsets", [])
                if isinstance(offsets, str):
                    offsets = [offsets]
                part_offset = poly_config.get("part_offset", "")
                if not offsets or not part_offset:
                    continue

                default = []
                alpha = []
                for i, offset in enumerate(offsets):
                    default.append(
                        poly_config.get("render_mode", "0xC4113878") if i < 1
                        else "RENDER_MODE_DEFAULT"
                    )
                    alpha.append("RENDER_MODE_ALPHA")

                parts[obj][poly] = {
                    "part":    part_offset,
                    "cmd":     offsets,
                    "default": default,
                    "alpha":   alpha,
                }

        if parts:
            self.character_cloaking_fix[character_folder] = {
                "struct_defs": [],
                "fix_defs": [],
                "clear_defs": [],
                "clear_asm": []
            }

            first_part = next(iter(parts))
            first_dpl_struct = f"custom_display_lists_struct_{character_folder.lower()}_{first_part.lower()}"
            self.character_cloaking_fix[character_folder]["fix_defs"].append(
                f"lli     t9, Character.id.{character_folder.upper()}\n\t\t"
                f"bne     t2, t9, pc() + 16\n\t\t"
                f"li      v0, {first_dpl_struct}\n\t\t"
                f"j       {dpl_config.get("fix_logic", "CharEnvColor.override_env_color_._fix")}\n\t\t"
                f"nop"
            )

            self.character_cloaking_fix[character_folder]["clear_defs"].append(
                f"nop\n\t\t"
                f"li      a1, custom_display_lists_struct_{character_folder.lower()}_{first_part.lower()}\n\t\t"
                f"lli     a2, Character.id.{character_folder.upper()}\n\t\t"
                f"beq     a0, a2, _clear{f"_{character_folder.lower()}" if len(
                    parts) > 1 else ""}"
            )

            for idx, obj in enumerate(parts):
                obj_config = parts[obj]
                hi_config = obj_config.get("hipoly", {})
                lo_config = obj_config.get("lopoly", {})
                hi_offsets = hi_config.get("cmd", [])
                lo_offsets = lo_config.get("cmd", [])

                self.character_cloaking_fix[character_folder]["struct_defs"].append(
                    f"scope custom_display_lists_struct_{character_folder.lower()}_{obj.lower()}: {{\n\t\t"
                    f"dw OS.FALSE     // 0x0000: initialized flag, high poly\n\t\t"
                    f"dw {"hi_default" if hi_offsets else "0x0"}  // 0x0004: pointer to default custom hi poly display list, or 0\n\t\t"
                    f"dw {"hi_alpha" if hi_offsets else "0x0"}    // 0x0008: pointer to alpha custom hi poly display list, or 0\n\t\t"
                    f"dh {hi_config.get("part", "0x0000")}       // 0x000C: offset to part in player struct\n\t\t"
                    f"dh {hi_offsets[0] if hi_offsets else "0xFFFF"}       // 0x000E: offset to 1st set render mode command for high poly\n\t\t"
                    f"dh {hi_offsets[1] if len(hi_offsets) > 1 else "0xFFFF"}       // 0x0010: offset to 2nd set render mode command for high poly, or -1\n\t\t"
                    f"dh {hi_offsets[2] if len(hi_offsets) > 2 else "0xFFFF"}       // 0x0012: offset to 3rd set render mode command for high poly, or -1\n\t\t"
                    f"dw OS.FALSE     // 0x0014: initialized flag, low poly\n\t\t"
                    f"dw {"lo_default" if lo_offsets else ("hi_default" if hi_offsets else "0x0")}  // 0x0018: pointer to default custom lo poly display list, or 0\n\t\t"
                    f"dw {"lo_alpha" if lo_offsets else ("hi_alpha" if hi_offsets else "0x0")}    // 0x001C: pointer to alpha custom lo poly display list, or 0\n\t\t"
                    f"dh {lo_config.get("part", hi_config.get("part", "0x0000"))}       // 0x0020: offset to part in player struct\n\t\t"
                    f"dh {lo_offsets[0] if lo_offsets else (hi_offsets[0] if hi_offsets else "0xFFFF")}       // 0x0022: offset to 1st set render mode command for high poly\n\t\t"
                    f"dh {lo_offsets[1] if len(lo_offsets) > 1 else (hi_offsets[1] if len(hi_offsets) > 1 else "0xFFFF")}       // 0x0024: offset to 2nd set render mode command for high poly, or -1\n\t\t"
                    f"dh {lo_offsets[2] if len(lo_offsets) > 2 else (hi_offsets[2] if len(hi_offsets) > 2 else "0xFFFF")}       // 0x0026: offset to 3rd set render mode command for high poly, or -1\n\t\t"
                    f"{f"hi_default:; create_custom_display_list({",".join(hi_config["default"])})\n\t\t" if hi_offsets else ""}"
                    f"{f"hi_alpha:; create_custom_display_list({",".join(hi_config["alpha"])})" if hi_offsets else ""}" +
                    f"{"\n\t\t" if lo_offsets else ""}"
                    f"{f"lo_default:; create_custom_display_list({",".join(lo_config["default"])})\n\t\t" if lo_offsets else ""}"
                    f"{f"lo_alpha:; create_custom_display_list({",".join(lo_config["alpha"])})" if lo_offsets else ""}"
                    "\n\t}\n"
                )

                if len(parts) > 1:
                    self.character_cloaking_fix[character_folder]["clear_asm"].append(
                        # if first part, add label
                        f"{f"_clear_{character_folder.lower()}:\n\t\t" if idx ==
                           0 else ""}"
                        f"// {obj.lower()}\n\t\t"

                        # if not first part, need to load part struct
                        f"{f"li      a1, custom_display_lists_struct_{character_folder.lower()}_{obj.lower()}\n\t\t" if idx !=
                           0 else ""}"

                        # if last part, branch to final clear
                        f"{"b       _clear\n\t\tnop\n\t\t" if idx == len(parts) - 1 else ""}"
                        # if not last part, clear as usual
                        f"{"sw      r0, 0x0000(a1)              // clear high poly initialized flag\n\t\t" if idx != len(parts) - 1 else ""}"
                        f"{"sw      r0, 0x0014(a1)              // clear low poly initialized flag" if idx != len(parts) - 1 else ""}"
                    )

        # Custom lineinfile patches from characters
        lif_files = []
        if os.path.exists(f"{original_path}/additions/"):
            lif_files = os.listdir(f"{original_path}/additions/")
            lif_files = [
                f"{original_path}/additions/{lif}" for lif in lif_files if lif.lower().endswith(".yaml")]
            lif_files.sort()

        for lif_yaml in lif_files:
            patches = yaml.safe_load(open(lif_yaml))

            for patch in patches:
                patch = patches.get(patch, {})

                patch_path = patch.get("path", "")
                patch_line = patch.get("line", [])
                patch_inserter = patch.get("type", "")
                patch_arg = patch.get("where", "")

                if not patch_path.startswith("src/") or not patch_path.endswith(".asm"):
                    continue

                if not os.path.exists(patch_path) or not patch_line:
                    continue

                if isinstance(patch_line, str):
                    patch_line = [patch_line]

                patch_line = patch.get("prefix", "") + \
                    patch.get("suffix", "").join(patch_line)

                self.character_lineinfile_patches.append([
                    patch_path,
                    patch_line,
                    patch_inserter,
                    patch_arg
                ])

        # Compile main.bin file
        # Replace attributes based on config
        with open(f"{original_path}/main.bin", 'rb') as binary_file:
            data = bytearray(binary_file.read())

        attr_offset = int(get_attrib_offset(
            './extra_characters/'+character_folder+'/main.bin'), 16)

        attr_sounds = {
            "death_fgm": 0xB4,
            "star_ko_fgm": 0xB8,
            "damaged_fgm": 0xBA,
            "attack_sfx_1": 0xBC,
            "attack_sfx_2": 0xBE,
            "attack_sfx_3": 0xC0,
            "heavy_lift_fgm": 0xE8
        }

        for sound_name, sound_pos in attr_sounds.items():
            if sound_name in config.get("attributes", {}):
                # SFX is the one defined in the config file
                sfx = config.get("attributes")[sound_name]

                # Replace logic for new sounds
                if sfx in config.get("sounds", {}):
                    # Replace the original 4 digits with the new ones
                    sfx = character_sound_add_list.get(
                        config['sounds'][sfx])

                # Replace id in final data
                data[attr_offset+sound_pos:attr_offset +
                     sound_pos+2] = bytes.fromhex(sfx)

        attr_values = {
            "size_multi": 0x0,
            "walk_1_cycle": 0x04,
            "walk_2_cycle": 0x08,
            "walk_3_cycle": 0x0C,
            "cargo_walk_1_cycle": 0x10,
            "cargo_walk_2_cycle": 0x14,
            "cargo_walk_3_cycle": 0x18,
            "walk_speed_multi": 0x20,
            "traction": 0x24,
            "dash_speed": 0x28,
            "dash_deceleration": 0x2C,
            "run_speed": 0x30,
            "jumpsquat_frames": 0x34,
            "jump_vel_x": 0x38,
            "jump_height_multi": 0x3C,
            "base_jump_height": 0x40,
            "aerial_jump_vel_x": 0x44,
            "aerial_jump_height": 0x48,
            "aerial_acceleration": 0x4C,
            "aerial_speed_max_x": 0x50,
            "aerial_friction": 0x54,
            "gravity": 0x58,
            "max_fall_speed": 0x5C,
            "fast_fall_speed": 0x60,
            "num_jumps": 0x64,
            "weight": 0x68,
            "jab_combo_frames": 0x6C,
            "dash_run_frames": 0x70,
            "shield_size": 0x74,
            "shield_break_vel_y": 0x78,
            "shadow_size": 0x7C,
            "push_range_width": 0x80,
            "push_range_x": 0x84,
            "vs_pause_zoom": 0x8C,
            "camera_y_offset": 0x90,
            "camera_zoom": 0x94,
            "default_camera_zoom": 0x98,
            "ecb_upper_y": 0x9C,
            "ecb_center_y": 0xA0,
            "ecb_bottom_y": 0xA4,
            "ecb_width": 0xA8,
            "ledge_grab_x": 0xAC,
            "ledge_grab_y": 0xB0
        }

        for attr_name, attr_pos in attr_values.items():
            if attr_name in config.get("attributes", {}):
                print(attr_name, config.get("attributes").get(
                    attr_name), hex_util.float_to_ieee754_hex(config.get("attributes").get(attr_name)))
                # Replace value in final data
                data[attr_offset+attr_pos:attr_offset +
                     attr_pos+4] = bytes.fromhex(hex_util.float_to_ieee754_hex(config.get("attributes").get(attr_name)))

        if config.get("attributes", {}).get("hurtboxes"):
            hurtboxes = config.get("attributes").get("hurtboxes")

            # Check defined more hurtboxes than allowed
            if len(hurtboxes) > 11:
                raise ValueError(
                    f"Too many hurtboxes defined for {character_folder}. Maximum is 11."
                )

            hurtbox_offset = attr_offset + 0x104

            # Print current hitboxes using the class
            for i in range(11):
                offset = hurtbox_offset + i * (9 * 4)
                print(Hurtbox.from_bytes(
                    data[offset:offset + 9*4]
                ).to_yaml())

            # Set all to disabled first
            for i in range(11):
                offset = hurtbox_offset + i * (9 * 4)
                data[offset:offset + 9*4] = \
                    Hurtbox.disabled().to_bytes()

            for i, hbox in enumerate(hurtboxes):
                hurtbox_def = Hurtbox(
                    bone=hbox["bone"],
                    height=hbox["height"],
                    grabbable=hbox["grabbable"],
                    offset=hbox["offset"],
                    size=hbox["size"]
                )

                offset = hurtbox_offset + i * (9 * 4)

                data[offset:offset + 9*4] = \
                    hurtbox_def.to_bytes()

        # Set action used for the opponent when using a throw
        if config.get("attributes", {}).get("forward_throw_animation") or config.get("attributes", {}).get("back_throw_animation"):
            table_offset_pointer = attr_offset + 0x338
            table_offset = int.from_bytes(
                data[table_offset_pointer+2:table_offset_pointer+4], byteorder="big") * 4

            fthrow = config.get("attributes", {}).get(
                "forward_throw_animation")

            if fthrow is not None:
                fthrow = int(fthrow, 16)

            bthrow = config.get("attributes", {}).get(
                "back_throw_animation")

            if bthrow is not None:
                bthrow = int(bthrow, 16)

            # thrown_status: array of FTThrownStatusArray (fttypes.h), one per
            # victim id, 16 bytes each - forward status1/status2 at +0/+4,
            # backward at +8/+12. status2 set to match status1.
            #
            # 26 of 27 real entries (54 FTThrownStatus / 2) - the 27th
            # overlaps a node in the external-file linked list below.
            for i in range(26):
                entry_offset = table_offset + i*16

                if fthrow is not None:
                    data[entry_offset:entry_offset+4] = fthrow.to_bytes(
                        4, 'big')
                    data[entry_offset+4:entry_offset +
                         8] = fthrow.to_bytes(4, 'big')

                if bthrow is not None:
                    data[entry_offset+8:entry_offset +
                         12] = bthrow.to_bytes(4, 'big')
                    data[entry_offset+12:entry_offset +
                         16] = bthrow.to_bytes(4, 'big')

        # Set texture-form entries (FTAttributes.textureparts_container), used by
        # the moveset "Set Texture Form" command to swap a model part between
        # alternate textures (e.g. facial expressions).
        # Each entry in config['attributes']['texture_forms'] is one literal container
        # entry (a list with one "0x0C" gives a single entry, like Mario; two gives
        # Fox's 2-entry setup). Every vanilla character with more than one texture form
        # for the same part follows the same pattern: the 1st occurrence of a part gets
        # {0x00, 0x00} and the 2nd occurrence gets {0x01, 0x01}, so that's assigned
        # automatically by occurrence order. The container normally has no spare room to
        # grow in place (it sits in the middle of main.bin, right before other structs),
        # so instead of resizing it in place we build a new container, append it to the
        # end of main.bin, and repoint the textureparts_container field at it.
        texture_forms = config.get("attributes", {}).get("texture_forms")
        if texture_forms:
            container = bytearray()
            part_occurrences = {}
            for part in texture_forms:
                part_id = int(part, 16)
                occurrence = part_occurrences.get(part_id, 0)
                part_occurrences[part_id] = occurrence + 1
                container += bytes([part_id, occurrence, occurrence])

            while len(container) % 4 != 0:
                container.append(0x00)

            while len(data) % 4 != 0:
                data.append(0x00)

            new_container_offset = len(data)
            data.extend(container)

            textureparts_pointer = attr_offset + 0x330
            data[textureparts_pointer+2:textureparts_pointer +
                 4] = (new_container_offset // 4).to_bytes(2, byteorder='big')

        # Update pointers to new external files we're adding to the main bin
        # From config->offsets->main, get the second number. That's the address for the first entry in a linked list for external files in data (main.bin file)
        # each entry has 2 parts: AAAABBBB where AAAA is the address of the next entry (divided by 4) and BBBB has some data that is written
        # First, let's go through the list until the last element, where AAAA will be "FFFF". Let's just print each entry until the last one
        offset = int(config['offsets']['main'][1], 16)
        pos = offset
        next_pos = None
        DEBUG_iters = 0

        while True:
            DEBUG_iters += 1
            if DEBUG_iters > 5000:
                raise RuntimeError(
                    f"External file linked list did not terminate for {character_folder} (stuck at pos {pos})")
            # Read next address (first 2 bytes)
            next_bytes = data[pos:pos+2]
            next_pos = int.from_bytes(next_bytes, byteorder='big') * 4

            if next_pos == 0xFFFF * 4:
                # Found end of list
                print(f"Last entry in the External file list: {pos:X}")

                # Calculate how many new entries we need
                num_new_entries = config.get("extend_main_reqlist", 0)

                if num_new_entries == 0:
                    break

                # Create new space at end of data
                original_size = len(data)
                new_size = original_size + \
                    (num_new_entries * 4)  # Each entry is 4 bytes
                data.extend(bytes(new_size - original_size))

                # Update current FFFF entry to point to first new entry
                data[pos:pos+2] = (original_size //
                                   4).to_bytes(2, byteorder='big')

                # Add new entries
                new_pos = original_size
                for i in range(num_new_entries):
                    # Set pointer to next entry
                    if i < num_new_entries - 1:
                        next_entry = new_pos + 4
                        data[new_pos:new_pos +
                             2] = (next_entry // 4).to_bytes(2, byteorder='big')
                    else:
                        # Last entry points to FFFF
                        data[new_pos:new_pos +
                             2] = (0xFFFF).to_bytes(2, byteorder='big')

                    # Set second part to 0000 for now
                    data[new_pos+2:new_pos +
                         4] = (0x0000).to_bytes(2, byteorder='big')

                    new_pos += 4

                break

            pos = next_pos

        with open(f"{output_path}/main.bin", 'wb') as binary_file:
            binary_file.write(data)

        # Check for sword trail definitions
        character_sword_trail_add_list = {}

        for i, (placeholder, data) in enumerate(config.get("sword_trails", {}).items()):
            self.sword_trail_add_list.append(
                f"add_sword_trail("
                f"{character_folder.upper()}_TRAIL_{placeholder}, "
                f"Character.id.{character_folder.upper()}, "
                f"{data['part']}, "
                f"{data['axis']}, "
                f"{data['color1']}, "
                f"{data['color2']}, "
                f"{data['start_pos']}, "
                f"{data['end_pos']})"
            )

            self.SWORD_TRAIL_COUNT += 1

            character_sword_trail_add_list[placeholder] = f"{
                (self.SWORD_TRAIL_COUNT + 1)*4:2X}"
        print(f"Sword trails to add: {character_sword_trail_add_list}")

        if os.path.exists(f"{original_path}/moveset/"):
            moveset_files = os.listdir(f"{original_path}/moveset/")
            moveset_files = [
                mf for mf in moveset_files if mf.lower().endswith(".bin")]
            moveset_files.sort()
        else:
            moveset_files = []

        # Do not allow sounds mapped with the highest bit set (0x8000 or higher)
        # as this bit is used in command D8 to indicate whether to play the original
        # sound along with the new one instead of overriding it
        for sfx_id, sfx_file_name in config.get("sounds", {}).items():
            sfx_int = int(sfx_id, 16)
            if sfx_int & 0x8000 != 0:
                logger.warning(
                    f"Invalid SFX ID 0x{sfx_id} in config['sounds']. SFX IDs greater than or equal to 0x8000 are not allowed, as the highest bit is used in command D8 to indicate whether to play the original sound along with the new one instead of overriding it."
                )

        for moveset_file in moveset_files:
            print(f"Compiling moveset file: {moveset_file}")

            with open(f"{original_path}/moveset/{moveset_file}", 'rb') as binary_file:
                data = bytearray(binary_file.read())

            pos = 0

            while pos < len(data):
                command = data[pos:pos+1].hex().upper()
                command_size = (COMMAND_SIZES.get(command) or 1) * 4

                # Replace sounds
                if command in ["38", "3C", "40", "44", "48", "4C", "50", "D8"]:
                    sfx = data[pos+2:pos+4].hex().upper()

                    if sfx in config.get("sounds", {}):
                        print(
                            f"REPLACING SFX: {command} - SOUND ID [{sfx}] -> [{config['sounds'][sfx]}](0x{character_sound_add_list.get(config['sounds'][sfx])})")
                        # Replace the original 4 digits with the new ones
                        new_sfx = bytes.fromhex(
                            character_sound_add_list.get(config['sounds'][sfx]))
                        data[pos+2:pos+4] = new_sfx
                    else:
                        sfx_int = int(sfx, 16)

                        if sfx_int > self.LAST_REMIX_SFX_ID and command != "D8":
                            logger.error(
                                f"Invalid SFX ID 0x{sfx} in {original_path}/moveset/{moveset_file}. "
                                f"SFX IDs greater than the last Remix SFX ID (0x{self.LAST_REMIX_SFX_ID:04X}) must be mapped through config['sounds']. "
                                f"Otherwise, a different character order would cause the SFX ID to point to a different sound than intended."
                            )

                # Replace sword trails
                if command in ["CC"]:
                    trail = data[pos+1:pos+2].hex().upper()

                    if trail in character_sword_trail_add_list:
                        data[pos+1:pos+2] = bytes.fromhex(
                            character_sword_trail_add_list[trail])

                        logger.info(f"REPLACING SWORD_TRAIL: {
                            trail} -> {character_sword_trail_add_list[trail]}")
                pos += command_size

            with open(f"{output_path}/moveset/{moveset_file}", 'wb') as binary_file:
                binary_file.write(data)

        # Check main asm syntax
        asm_util.validate_asm(f"{original_path}/main.asm")

        # For any included files in main.asm, check syntax as well
        included_files = asm_util.get_included_files(
            f"{original_path}/main.asm")

        for included_file in included_files:
            included_file_path = os.path.join(os.path.dirname(
                f"{original_path}/main.asm"), included_file)
            asm_util.validate_asm(included_file_path)

        # Compile main asm
        main_data = open(
            f"{original_path}/main.asm", 'r', encoding="utf-8").readlines()

        with open(f"{output_path}/main.asm", 'w', encoding="utf-8") as main_compiled_file:
            appended_movesets = False

            for line in main_data:
                main_compiled_file.write(line)

                if line.lstrip().startswith("scope") and not appended_movesets:
                    main_compiled_file.write(
                        "\t// Moveset files, auto generated\n")
                    for moveset_file in moveset_files:
                        moveset_file = moveset_file.split(".")[0]
                        main_compiled_file.write(
                            f'\tmoveset_{moveset_file}:\n'
                        )
                        main_compiled_file.write(
                            f'\tinsert {moveset_file},"moveset/{moveset_file}.bin"\n')
                    main_compiled_file.write("\n")

                    if len(config.get('sounds', {})) > 0:
                        main_compiled_file.write(
                            "\t// Sound IDs, auto generated\n")

                        main_compiled_file.write("\tscope FGM {\n")

                        for (sfx_name, sfx_id) in character_sound_add_list.items():

                            main_compiled_file.write(
                                f'\t\tconstant {sfx_name.upper()}(0x{sfx_id})\n')

                        main_compiled_file.write("\t}\n\n")

                    if len(extra_files_merged_offsets_str) > 0:
                        main_compiled_file.write(
                            "\t// External file offsets, auto generated\n")
                        main_compiled_file.write(
                            "\tscope FILE_OFFSETS {\n")
                        for offset in extra_files_merged_offsets_str:
                            main_compiled_file.write(f"\t\t{offset}\n")
                        main_compiled_file.write("\t}\n\n")

                    charge_smash_frames = {
                        "forward": config.get('charge_smash_frames', {}).get('forward', '0'),
                        "up": config.get('charge_smash_frames', {}).get('up', '0'),
                        "down": config.get('charge_smash_frames', {}).get('down', '0'),
                        "unused": 0
                    }

                    main_compiled_file.write(
                        "\t// Charged smash attack frame data\n\tOS.align(4)\n\tcharge_smash_frames:\n")

                    for direction in charge_smash_frames:
                        main_compiled_file.write(
                            f'\tdb {charge_smash_frames[direction]}\t// {direction}\n')

                    main_compiled_file.write(
                        f'\tChargeSmashAttacks.set_charged_smash_attacks(Character.id.{character_folder.upper()}, charge_smash_frames)\n\n')

                    appended_movesets = True
