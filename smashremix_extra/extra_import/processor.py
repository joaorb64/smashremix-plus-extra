import os
import shutil
import yaml

from smashremix_extra.file_manager import FileManager
from smashremix_extra.logger import logger
from smashremix_extra.stage.processor import StageProcessor

# Generated file with ExtraImports.load_file_ / ExtraImports.load_files_ (included in main.asm)
EXTRA_IMPORTS_ASM = "build/extra_imports/ExtraImports.asm"

# Built-in imports shipped with the appender, available to every project
BUILTIN_IMPORTS_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "extra_imports"))


class ExtraImportProcessor:
    """Processes the data folders of extra imports.

    An extra import `extra_imports/<Name>.asm` can have a folder
    `extra_imports/<Name>/` with a config.yaml declaring:

    - files: [[name, tableOffset, resourceOffset, {footer, trim}], ...]
      Same format as a stage's config.yaml `files:`. Each <name>.bin (and
      optional <name>_reqlist.txt, with ${NAME} tokens for the other files)
      lives in the folder. These files are loaded at the start of every battle.
    - sounds: {name: {sample_rate, fgm_type, reverb, length}}
      Same format as a stage's config.yaml `sounds:`. Each sounds/<name>.aifc
      (or .wav, converted automatically) lives in the folder.
    - textures: {name: {file: <image>.png, format: rgba32|ia8, in_file: bool}}
      Converted at build time and inserted into the code, so they're always in
      RAM. Sides must be powers of 2 and fit in TMEM (rgba32 up to 32x32, ia8 up
      to 64x64). See _convert_texture for the generated constants.
      With in_file: true, the texture goes in a generated file instead (all of an
      import's in_file textures share one), loaded at the start of every battle
      like files:. Its address is file + TEXTURES.<TEX>.OFFSET, where the file
      address comes from TEXTURES.FILE.ptr or lbRelocFindForceStatusBufferFile
      (0x800CD698) with TEXTURES.FILE.id (0 outside battles).

    For each import this generates build/extra_imports/<Name>/generated.asm,
    included before <Name>.asm, with:

        scope <Name> {
            scope FILES { scope <FILE> { constant id(..); ptr: dw 0; <offsets> } }
            scope FGM { constant <SOUND>(..) }
            scope TEXTURES {
                scope FILE { constant id(..); ptr: dw 0 }     // if there are in_file textures
                scope <TEX> { constant WIDTH(..); constant HEIGHT(..); data: <texels> }  // or constant OFFSET(..) if in_file
            }
            scope load_files_: { ... }   // fills each FILES.<FILE>.ptr
        }

    so the import references them as <Name>.FILES.<FILE>.ptr / <Name>.FGM.<SOUND> /
    <Name>.TEXTURES.<TEX>.data.
    """

    def __init__(self):
        # add_sound(...) strings spliced into src/FGM.asm by the appender
        self.sound_add_list = []
        # Running FGM/SFX id counter, seeded by the appender after stage sounds
        self.LAST_SFX_ID = 0
        # Names of imports with a generated.asm
        self.generated = []

    @staticmethod
    def import_dirs():
        """Folders with extra imports: the project's extra_imports/ and the
        appender's built-in ones. A project import overrides a built-in one
        with the same name."""
        dirs = [os.path.relpath("./extra_imports")]
        builtin = os.path.relpath(BUILTIN_IMPORTS_DIR)
        if os.path.normcase(os.path.abspath(builtin)) != os.path.normcase(os.path.abspath(dirs[0])):
            dirs.append(builtin)
        return [d for d in dirs if os.path.isdir(d)]

    @staticmethod
    def list_imports():
        """{Name: folder} of extra imports (<folder>/<Name>.asm), sorted by name."""
        found = {}
        for d in ExtraImportProcessor.import_dirs():
            for f in os.listdir(d):
                if f.endswith(".asm") and f[:-4] not in found:
                    found[f[:-4]] = d
        return dict(sorted(found.items()))

    def process_all(self):
        for name, folder in self.list_imports().items():
            if os.path.isfile(f"{folder}/{name}/config.yaml"):
                self.process(name, folder)
        self._write_extra_imports_asm()

    def process(self, name, folder="./extra_imports"):
        original_path = f"{folder}/{name}"
        output_path = f"build/extra_imports/{name}"
        config = yaml.safe_load(open(f"{original_path}/config.yaml", encoding="utf-8")) or {}

        shutil.copytree(original_path, output_path, dirs_exist_ok=True)

        file_blocks = []
        file_names = []
        imported_file_ids = {}

        for entry in config.get("files", []) or []:
            # [name, tableOffset, resourceOffset, {opts}] - see StageProcessor
            fname = entry[0]
            tbl_off = entry[1] if len(entry) > 1 else "3FFFC"
            res_off = entry[2] if len(entry) > 2 else "3FFFC"
            opts = entry[3] if len(entry) > 3 and isinstance(entry[3], dict) else {}

            if opts.get("trim"):
                tbl_off = StageProcessor._trim_ge_model(
                    output_path, fname, tbl_off, opts["trim"], opts.get("footer"))

            has_reqlist = os.path.exists(f"{original_path}/{fname}_reqlist.txt")
            import_file = FileManager.add_file(
                path=f"{output_path}/{fname}.bin",
                name=f"extra_import_{name}_file_{fname}",
                internal_file_table_offset=tbl_off,
                internal_file_resource_offset=res_off,
                reqlist_path=f"{output_path}/{fname}_reqlist.txt" if has_reqlist else None,
                compression_level=1
            )
            imported_file_ids[fname] = import_file.id
            file_names.append(fname.upper())

            lines = [f"        scope {fname.upper()} {{",
                     f"            constant id(0x{import_file.id:X})",
                     "            OS.align(4)",
                     "            ptr:",
                     "            dw 0"]
            for oname, ooff in StageProcessor._model_offsets(
                    output_path, fname, opts.get("footer")).items():
                lines.append(f"            constant {oname}(0x{ooff:X})")
            lines.append("        }")
            file_blocks.append("\n".join(lines))

        # ${NAME} tokens in each file's reqlist -> "<id> <file_name>"
        for fname in imported_file_ids:
            compiled_path = f"{output_path}/{fname}_reqlist.txt"
            if not os.path.exists(compiled_path):
                continue
            with open(compiled_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            with open(compiled_path, "w", encoding="utf-8") as f:
                for line in lines:
                    for dep_name, dep_id in imported_file_ids.items():
                        line = line.replace(
                            "${" + dep_name.upper() + "}",
                            f"{dep_id:04X} extra_import_{name}_file_{dep_name}")
                    f.write(line)

        sounds = config.get("sounds", {}) or {}
        from smashremix_extra.audio import vadpcm
        if os.path.isdir(f"{output_path}/sounds"):
            vadpcm.convert_dir(
                f"{output_path}/sounds",
                rate_for=lambda n: (sounds.get(n) or {}).get("sample_rate", 16000))

        fgm_constants = []
        for sname, settings in sounds.items():
            settings = settings or {}
            self.LAST_SFX_ID += 1
            fgm_constants.append(f"        constant {sname.upper()}(0x{self.LAST_SFX_ID:04X})")
            self.sound_add_list.append(
                f"add_sound(../{output_path}/sounds/{sname}, "
                f"SAMPLE_RATE_{settings.get('sample_rate', 16000)}, "
                f"FGM_TYPE_{settings.get('fgm_type', 'VOICE')}, "
                f"{settings.get('reverb', 0)}, {settings.get('length', -1)})")

        texture_blocks = []
        bundled = bytearray()                                     # in_file textures' texels
        for tname, settings in (config.get("textures", {}) or {}).items():
            settings = settings or {}
            texture_blocks.append(self._convert_texture(
                name, output_path, tname, settings,
                bundle=bundled if settings.get("in_file") else None))

        texture_file = None
        if bundled:
            with open(f"{output_path}/textures.bin", "wb") as f:
                f.write(bundled)
            texture_file = FileManager.add_file(
                path=f"{output_path}/textures.bin",
                name=f"extra_import_{name}_textures",
                internal_file_table_offset="3FFFC",               # no pointers to relocate
                internal_file_resource_offset="3FFFC",
                compression_level=1
            )
            texture_blocks.insert(0, "\n".join([
                "        scope FILE {",
                f"            constant id(0x{texture_file.id:X})",
                "            OS.align(4)",
                "            ptr:",
                "            dw 0",
                "        }"]))

        loader = ["    // @ Description",
                  "    // Loads this import's files (called at the start of every battle)",
                  "    scope load_files_: {",
                  "        OS.routine_begin(0x20)"]
        for fname in file_names:
            loader += [f"        lli     a0, FILES.{fname}.id",
                       f"        li      a1, FILES.{fname}.ptr",
                       "        jal     ExtraImports.load_file_",
                       "        nop"]
        if texture_file:
            loader += ["        lli     a0, TEXTURES.FILE.id",
                       "        li      a1, TEXTURES.FILE.ptr",
                       "        jal     ExtraImports.load_file_",
                       "        nop"]
        loader += ["        OS.routine_end(0x20)",
                   "    }"]

        blocks = ["// Auto-generated from config.yaml files: / sounds: / textures: (do not edit)",
                  f"scope {name} {{"]
        if file_blocks:
            blocks.append("    scope FILES {\n" + "\n".join(file_blocks) + "\n    }")
        if fgm_constants:
            blocks.append("    scope FGM {\n" + "\n".join(fgm_constants) + "\n    }")
        if texture_blocks:
            blocks.append("    scope TEXTURES {\n" + "\n".join(texture_blocks) + "\n    }")
        blocks.append("\n".join(loader))
        blocks.append("}")

        with open(f"{output_path}/generated.asm", "w", encoding="utf-8") as f:
            f.write("\n".join(blocks) + "\n")

        self.generated.append(name)
        logger.info(f"Extra import {name}: {len(file_names)} file(s), {len(fgm_constants)} sound(s)")

    @staticmethod
    def _convert_texture(name, output_path, tname, settings, bundle=None):
        """Converts a PNG to an N64 texture and emits its constants, including the
        RDP command words to load it (so display lists don't depend on the format):
        TIMG_W0 (set texture image), LOAD_TILE_W0, LOAD_BLOCK_W1, RENDER_TILE_W0 and
        TILE_SIZE_W1 (render tile 0, clamped).

        Formats:
        - rgba32: full color, 8-bit alpha. Up to 32x32.
        - ia8: 4-bit intensity (luminance) + 4-bit alpha. Up to 64x64. Meant to be
          tinted with the primitive color.

        With bundle (a bytearray), the texels are appended to it (8-byte aligned)
        and the texture gets an OFFSET constant instead of the data label.
        """
        from PIL import Image
        src = f"{output_path}/{settings.get('file', tname + '.png')}"
        fmt = str(settings.get("format", "rgba32")).lower()
        img = Image.open(src).convert("RGBA")
        w, h = img.size
        if w & (w - 1) or h & (h - 1):
            raise ValueError(f"{name}: texture {tname} must have power-of-2 sides, got {w}x{h}")

        pixels = list(img.getdata())
        if fmt == "rgba32":
            raw = img.tobytes()                                   # R, G, B, A per pixel
            texels_per_word = 1
            tmem_bytes = w * h * 4
            timg_w0 = 0xFD180000                                  # RGBA 32b
            load_tile_w0 = 0xF5180000
            load_texels = w * h
            dxt_words = max(1, w * 4 // 8)
            render_tile_w0 = 0xF5180000 | (((w * 2 + 7) >> 3) << 9)
        elif fmt == "ia8":
            raw = bytes(
                (round((0.299 * r + 0.587 * g + 0.114 * b) / 255 * 15) << 4) | round(a / 255 * 15)
                for (r, g, b, a) in pixels)
            tmem_bytes = w * h
            timg_w0 = 0xFD700000                                  # IA, loaded as 16b
            load_tile_w0 = 0xF5700000
            load_texels = (w * h + 1) >> 1
            dxt_words = max(1, w // 8)
            render_tile_w0 = 0xF5680000 | (((w + 7) >> 3) << 9)  # IA 8b
        else:
            raise ValueError(f"{name}: texture {tname} has unknown format {fmt}")

        if tmem_bytes > 4096 or load_texels > 2048:
            raise ValueError(f"{name}: texture {tname} ({w}x{h} {fmt}) doesn't fit in TMEM")

        dxt = ((1 << 11) + dxt_words - 1) // dxt_words
        load_block_w1 = 0x07000000 | ((load_texels - 1) << 12) | dxt
        tile_size_w1 = ((w - 1) << 14) | ((h - 1) << 2)

        if bundle is not None:
            bundle.extend(b"\0" * (-len(bundle) % 8))           # load block needs 8-byte alignment
            location = [f"            constant OFFSET(0x{len(bundle):X})"]
            bundle.extend(raw)
        else:
            out_name = f"{tname}.{fmt}"
            with open(f"{output_path}/{out_name}", "wb") as f:
                f.write(raw)
            location = ["            OS.align(8)",
                        f"            insert data, \"{out_name}\""]
        return "\n".join([
            f"        scope {tname.upper()} {{",
            f"            constant WIDTH({w})",
            f"            constant HEIGHT({h})",
            f"            constant TIMG_W0(0x{timg_w0:08X})",
            f"            constant LOAD_TILE_W0(0x{load_tile_w0:08X})",
            f"            constant LOAD_BLOCK_W1(0x{load_block_w1:08X})",
            f"            constant RENDER_TILE_W0(0x{render_tile_w0:08X})",
            f"            constant TILE_SIZE_W1(0x{tile_size_w1:08X})",
            *location,
            "        }"])

    def _write_extra_imports_asm(self):
        os.makedirs(os.path.dirname(EXTRA_IMPORTS_ASM), exist_ok=True)
        calls = []
        for name in self.generated:
            calls += [f"        jal     {name}.load_files_", "        nop"]
        content = "\n".join([
            "// Auto-generated (do not edit)",
            "scope ExtraImports {",
            "    // @ Description",
            "    // Loads a file and writes its RAM address to a word, unless it's already loaded",
            "    // a0 - file id, a1 - address of the word",
            "    scope load_file_: {",
            "        OS.routine_begin(0x20)",
            "        sw      a0, 0x0018(sp)",
            "        sw      a1, 0x001C(sp)",
            "        jal     0x800CD698                  // lbRelocFindForceStatusBufferFile(a0)",
            "        nop",
            "        bnez    v0, _store                  // if already loaded, use it",
            "        lw      a1, 0x001C(sp)",
            "        jal     Render.load_file_           // loads and writes the address to *a1",
            "        lw      a0, 0x0018(sp)",
            "        b       _end",
            "        nop",
            "        _store:",
            "        sw      v0, 0x0000(a1)",
            "        _end:",
            "        OS.routine_end(0x20)",
            "    }",
            "",
            "    // @ Description",
            "    // Loads every extra import's files. Called at the start of every battle.",
            "    scope load_files_: {",
            "        OS.routine_begin(0x20)",
            *calls,
            "        OS.routine_end(0x20)",
            "    }",
            "}",
            ""])
        with open(EXTRA_IMPORTS_ASM, "w", encoding="utf-8") as f:
            f.write(content)
