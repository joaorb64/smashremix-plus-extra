#!/usr/bin/env python3
"""
Injects an extra character over their base character, producing a ROM GE
can open directly to edit their model/animations.

Wraps gen_editable_rom.py, but fixes up file IDs using
filename_overrides_extra.txt - the vanilla-scoped IDs in src/File.asm only
match a build containing every extra character.

Known issue: GE adds/removes a character's Special Parts based on which
moveset commands reference them, so a part can come in on the wrong bone or
be missing. Fix by manually editing the movesets in GE: remove unused
"Set Model Form" commands and add correct ones for every part in use - the
model then updates correctly.

Usage:
    python3 scripts/gen_editable_rom_extra.py --character TERRY \\
        --rom /path/to/ssb64asm_extra.z64 --output terry_over_falcon.z64
"""
import argparse
import os
import re
import shutil
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
APPENDER_DIR = os.path.dirname(SCRIPTS_DIR)
# smashremix is a submodule, not ours to edit - only used for its
# roms/build/assembler assets. gen_editable_rom.py and SSB.py live here
# in appender/scripts/ instead, so they're actually tracked by our repo.
SMASHREMIX_DIR = os.path.join(APPENDER_DIR, "smashremix")
# character_appender.py builds against the content repo embedding appender/
# as a submodule (extra_characters/, src/, main.asm, build/ live there).
CONTENT_ROOT = os.path.dirname(APPENDER_DIR)


def leading_zero_word_offset(data):
    """Count leading 4-byte zero words in an animation file's data."""
    offset = 0
    i = 0
    while i + 4 <= len(data):
        if int.from_bytes(data[i:i+4], 'big') == 0:
            offset += 1
            i += 4
        else:
            break
    return offset * 4


def load_extra_overrides(overrides_path):
    """Map CONSTANT_NAME -> file ID, for one specific extra ROM build."""
    overrides = {}
    pattern = re.compile(r'^([0-9A-Fa-f]{4})\s+([A-Z0-9_]+)$')
    with open(overrides_path, "r", encoding="utf-8") as f:
        for line in f:
            match = pattern.match(line.strip())
            if match:
                file_id, name = match.groups()
                overrides[name] = int(file_id, 16)
    return overrides


def find_animation_definitions(character_name, roots):
    """Same scan as gen_editable_rom.get_character_animation_definitions,
    but also checks extra_characters/<Name>/main.asm, not just src/."""
    animation_entries = []

    for root_dir in roots:
        if not os.path.isdir(root_dir):
            continue
        for root, _, files in os.walk(root_dir):
            for file in files:
                if not file.endswith('.asm'):
                    continue
                with open(os.path.join(root, file), 'r', encoding='utf-8') as f:
                    lines = [
                        line for line in f if not line.strip().startswith('//')]
                    content = ''.join(lines)

                    patterns = [
                        rf'Character\.(edit_action_parameters)\(\s*{character_name}\s*,\s*([^,]+),\s*File\.([^,]+).*?,.*?(0x[0-9A-Fa-f]+|\-1|\d+)\)',
                        rf'Character\.(edit_menu_action_parameters)\(\s*{character_name}\s*,\s*([^,]+),.*?File\.([^,]+).*?,\s*(0x[0-9A-Fa-f]+|\-1|\d+)\)'
                    ]

                    for pattern in patterns:
                        matches = re.finditer(pattern, content, re.MULTILINE)
                        for match in matches:
                            func, action_name, file_name, flags = match.groups()
                            if file_name != '-1':
                                animation_entries.append((
                                    file_name,
                                    action_name.split(".")[-1],
                                    int(flags, 16) if flags != '-1' else -1,
                                    func
                                ))

    return animation_entries


def find_local_action_constants(character_name, roots):
    """Extra characters can declare custom action IDs in a local
    `scope Action { constant USP(0x0DC) ... }` block. gen_editable_rom.py's
    ACTIONS dict doesn't know these, so pull them out here to merge in."""
    local_actions = {}
    scope_pattern = re.compile(r'scope\s+Action\s*:?\s*\{(.*?)\n\s*\}', re.DOTALL)
    const_pattern = re.compile(r'constant\s+(\w+)\((0x[0-9A-Fa-f]+)\)')

    for root_dir in roots:
        if not os.path.isdir(root_dir):
            continue
        for root, _, files in os.walk(root_dir):
            for file in files:
                if not file.endswith('.asm'):
                    continue
                path = os.path.join(root, file)
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()
                if f'Character.edit_action_parameters({character_name}' not in content and \
                        f'Character.edit_menu_action_parameters({character_name}' not in content:
                    continue
                for scope_body in scope_pattern.findall(content):
                    for name, value in const_pattern.findall(scope_body):
                        local_actions[name] = int(value, 16)

    return local_actions


def find_character_moveset_dir(character_name, roots):
    """Find the extra_characters/<Name>/moveset/ folder for this character,
    by locating the main.asm that actually declares its actions."""
    for root_dir in roots:
        if not os.path.isdir(root_dir):
            continue
        for root, _, files in os.walk(root_dir):
            if 'main.asm' not in files:
                continue
            with open(os.path.join(root, 'main.asm'), 'r', encoding='utf-8') as f:
                content = f.read()
            if f'Character.edit_action_parameters({character_name}' in content:
                moveset_dir = os.path.join(root, 'moveset')
                if os.path.isdir(moveset_dir):
                    return moveset_dir
    return None


def find_special_part_forms(moveset_dir):
    """Scans every moveset/*.bin for "Set Model Form" commands (event 40)
    and collects every (bone_id, form) pair found, in order. Just a
    fixed-4-byte scan, not a real disassembler - good enough since we only
    need to know which bones/forms exist."""
    seen = set()
    pairs = []
    for name in sorted(os.listdir(moveset_dir)):
        if not name.lower().endswith('.bin'):
            continue
        with open(os.path.join(moveset_dir, name), 'rb') as f:
            data = f.read()
        for i in range(0, len(data) - 3, 4):
            word = int.from_bytes(data[i:i+4], 'big')
            if (word >> 26) & 0x3F != 40:  # nFTMotionEventSetModelPartID
                continue
            bone_id = (word >> 19) & 0x7F
            form = word & 0x7FFFF
            pair = (bone_id, form)
            if pair not in seen:
                seen.add(pair)
                pairs.append(pair)
    return pairs


def main():
    parser = argparse.ArgumentParser(
        description="Generates a vanilla ROM with an extra character injected over their base character for editing.")
    parser.add_argument('--character', required=True,
                        help="Extra character name as defined in src/Character.asm, e.g. TERRY")
    parser.add_argument('--rom', required=True,
                        help="Path to the built extra ROM (e.g. ssb64asm_extra.z64) containing the character's data")
    parser.add_argument('--vanilla', default=os.path.join(SMASHREMIX_DIR, "roms", "ssb.rom"),
                        help="Path to the vanilla Smash 64 ROM file")
    parser.add_argument('--overrides', default=os.path.join(SMASHREMIX_DIR, "roms", "filename_overrides_extra.txt"),
                        help="filename_overrides_extra.txt matching --rom's build")
    parser.add_argument('--output', default='editable_rom.z64',
                        help='Output ROM file path')
    parser.add_argument('--export-animations', action='store_true',
                        help="Export the character's action animation files to an "
                        "animations/ folder next to the output ROM. Off by default; "
                        "these are extracted from --rom only and never injected into "
                        "the output ROM.")
    parser.add_argument('--animations-dir', default=None,
                        help='Where to export animations to when --export-animations is set '
                        '(default: an "animations" folder next to --output)')
    args = parser.parse_args()

    output_path = os.path.abspath(args.output)
    rom_path = os.path.abspath(args.rom)
    vanilla_path = os.path.abspath(args.vanilla)
    overrides_path = os.path.abspath(args.overrides)
    animations_dir = os.path.abspath(args.animations_dir) if args.animations_dir else os.path.join(
        os.path.dirname(output_path), "animations")

    workdir = tempfile.mkdtemp(prefix="gen_editable_rom_extra_")
    try:
        # gen_editable_rom.py hardcodes src/, build/, roms/, assembler/
        # relative to cwd, so assemble a workdir with what it expects.
        os.symlink(os.path.join(CONTENT_ROOT, "src"),
                   os.path.join(workdir, "src"))
        os.symlink(os.path.join(SMASHREMIX_DIR, "build"),
                   os.path.join(workdir, "build"))
        os.symlink(os.path.join(SMASHREMIX_DIR, "assembler"),
                   os.path.join(workdir, "assembler"))
        os.symlink(os.path.join(SMASHREMIX_DIR, "roms"),
                   os.path.join(workdir, "roms"))

        sys.path.insert(0, SCRIPTS_DIR)  # gen_editable_rom.py
        sys.path.insert(0, APPENDER_DIR)  # SSB.py, which it imports
        os.chdir(workdir)

        import gen_editable_rom as ger

        overrides = load_extra_overrides(overrides_path)
        character_prefix = f"{args.character}_"
        for name, file_id in overrides.items():
            if name.startswith(character_prefix):
                ger.FILES[name] = file_id

        class RunArgs:
            character = args.character
            rom = rom_path
            vanilla = vanilla_path
            output = os.path.basename(output_path)

        ergen = ger.EditableROMGenerator(RunArgs())

        # file_shield may be a raw hex ID rather than a File.xxx name; if it's
        # this character's own shield pose, rewrite it as File.xxx so it gets injected.
        shield_field = ergen.character['file_shield']
        if isinstance(shield_field, str) and not shield_field.startswith('File.'):
            own_shield_name = f"{args.character}_SHIELD_POSE"
            if own_shield_name in overrides:
                ergen.character['file_shield'] = f"File.{own_shield_name}"

        animation_roots = [os.path.join(workdir, "src"),
                          os.path.join(CONTENT_ROOT, "extra_characters")]
        ergen.get_character_animation_definitions = lambda: find_animation_definitions(
            ergen.character["name"], animation_roots
        )

        # Merge in this character's own local scope-Action constants (custom
        # action IDs like USP/TORNADO_START >= 0xDC) so create_rom() can
        # resolve them instead of treating them as unrecognized names.
        ger.ACTIONS.update(find_local_action_constants(
            ergen.character["name"], animation_roots))

        try:
            ergen.create_rom()
        finally:
            ergen.cleanup()

        # create_rom() always dumps animations to workdir/animations/; only
        # copy them out if requested, else they're discarded with workdir.
        generated_animations_dir = os.path.join(workdir, "animations")
        if args.export_animations and os.path.isdir(generated_animations_dir):
            os.makedirs(animations_dir, exist_ok=True)
            shutil.copytree(generated_animations_dir,
                            animations_dir, dirs_exist_ok=True)

        generated = os.path.join(workdir, os.path.basename(output_path))

        # Recompute internal_file_table_offset for every injected animation
        # and re-inject any that are wrong.
        from smashremix_extra.injector.injector import ROMInjector

        fixups = []
        for file_name, action_name, _flags, func in ergen.get_character_animation_definitions():
            if func == 'edit_menu_action_parameters':
                # create_rom() leaves these untouched (overwriting them
                # crashes character select in Training mode), so there's
                # nothing here to offset-correct either.
                continue
            if action_name in ger.ACTIONS:
                action_id = ger.ACTIONS[action_name]
            else:
                try:
                    action_id = int(action_name, 16)
                except ValueError:
                    # Action name that doesn't resolve to a vanilla action ID,
                    # a locally-declared custom action ID, or a raw hex ID.
                    continue
            action_file = ergen.get_action_animation_file(action_id, func)
            if not action_file or action_file > 0x853:
                continue
            data = ergen.entries[ger.FILES[file_name]].extract('data')
            correct_offset = leading_zero_word_offset(data)
            fixups.append((action_file, file_name, data, correct_offset))

        if fixups:
            injector = ROMInjector(generated, generated)
            fixed = []
            for action_file, file_name, data, correct_offset in fixups:
                if injector.entries[action_file].tbl != correct_offset:
                    tmp_path = os.path.join(workdir, "_animfix.bin")
                    with open(tmp_path, "wb") as f:
                        f.write(data)
                    injector.modify(action_file, tmp_path,
                                     correct_offset, 0x3FFFC, compression_level=0)
                    fixed.append(
                        f"{file_name} (file 0x{action_file:04X}, offset -> 0x{correct_offset:04X})")
            if fixed:
                print(
                    f"Correcting internal_file_table_offset for {len(fixed)} animation(s):")
                for line in fixed:
                    print(f"  {line}")
                injector.save(on_progress=print)
                ger.run_windows_command(f"assembler/rn64crc.exe -u {generated}")

        # Report (don't inject) the "Set Model Form" commands this
        # character's special parts need - every attempt to inject these
        # into file 0xE8 automatically has crashed GE on load for reasons
        # that weren't pinned down, so this is left as a manual step: add
        # these to Jab1 (or wherever) yourself in GE.
        moveset_dir = find_character_moveset_dir(
            ergen.character["name"], animation_roots)
        part_forms = find_special_part_forms(
            moveset_dir) if moveset_dir else []
        # 0x7FFFF is the 19-bit "-1"/default-form sentinel - not a real
        # form to set, so it's not worth listing.
        part_forms = sorted(
            (pf for pf in part_forms if pf[1] != 0x7FFFF))

        if part_forms:
            print(
                f"\n{len(part_forms)} Set Model Form command(s) to add manually in GE "
                f"for {ergen.character['name']}'s special parts:")
            for bone_id, form in part_forms:
                print(f"  bone {bone_id}, form {form}")

        shutil.move(generated, output_path)
    finally:
        os.chdir(APPENDER_DIR)
        shutil.rmtree(workdir, ignore_errors=True)

    print(f"\nDone. Output ROM: {output_path}")


if __name__ == "__main__":
    main()
