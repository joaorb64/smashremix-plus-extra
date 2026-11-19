"""Kirby copy abilities of extra characters.

A character folder may have:
  kirby_hat/kirby_hat.bin    the hat Kirby wears after inhaling the character (Kirby's body part
                             with the hat), and optionally what he holds while the copied special
                             shows its "gun" (appender/scripts/ssb64_model/kirby_hat.py; edited in
                             Blender through ssb64_model.py export / import). Added as is.
  kirby_hat/animations/*.bin Kirby's animations for the copied moves: File.<NAME>_KIRBY_ANIM_<file>
  Kirby's actions      in the character's own code: Character.add_new_action(_params)(KIRBY / JKIRBY, ...)
                       (in their own scopes, e.g. scope Cloud { scope KirbyCopy { ... } }); the
                       build grows Kirby's / J Kirby's action arrays to fit. The characters' code is
                       assembled after Kirby / J Kirby, so these come after Remix's Kirby actions
                       (Kirby.asm tells his new actions from vanilla ones by id).

How a hat gets into the game (no Remix file is replaced):
  - The character's main file gets one more external pointer (the last of its chain), to its hat
    file's start: the hat file loads (and relocates) with the character, in matches only (the CSS
    loads the character's model alone), and Kirby only copies characters that are in the match.
    KirbyHats.asm finds it through that pointer (Character.<NAME>_file_1_ptr: the loaded main
    file), like Remix's own hat files through Kirby's character file (0x1D810 / 0x1D814).
    (Kirby's file carrying every hat made the CSS run out of memory.)
  - KirbyHats.asm: add_hat() entries (based on the plain Kirby, overriding display list, MObjSub
    list and costume scripts with the hat file's), the hat-file lookup by file index (now a
    table: Remix's 2 files, then ours), the "gun" swap (model part on joint 0x11) for hats with
    an item, the hat id in Kirby's inhale table.
"""
import os
import re
import struct
import sys

import yaml

import lineinfile

from smashremix_extra.constants import SMASHREMIX_PATH
from smashremix_extra.file_manager import FileManager
from smashremix_extra.logger import logger

KIRBY_CHAR_FILE = 0x148
ROM = os.path.join(SMASHREMIX_PATH, "roms/original.z64")
KIRBY_HATS_ASM = "src/KirbyHats.asm"
PART_SIZE = 0x14
HEADER_SIZE = 4 * PART_SIZE


def hat_file_offsets(data):
    """kirby_hat.bin (its KHAT / KHT2 trailer, see scripts/ssb64_model/kirby_hat.py) -> (offsets, chain
    head). offsets["extras"]: the extra hats' (more variants of the hat: Kirby's body + other triangles)."""
    if data[-4:] == b"KHAT":
        n_extra, end = 0, len(data) - 8
    elif data[-4:] == b"KHT2":
        n_extra, end = struct.unpack_from(">I", data, len(data) - 12)[0], len(data) - 12
    else:
        raise SystemExit("kirby_hat.bin: no KHAT / KHT2 trailer (not made by kirby_hat.py)")
    head = struct.unpack_from(">I", data, len(data) - 8)[0]
    ptrs, node, seen = {}, head, set()
    while node not in seen and node + 4 <= len(data):
        seen.add(node)
        nxt, tgt = struct.unpack_from(">HH", data, node)
        ptrs[node] = tgt * 4
        if nxt == 0xFFFF:
            break
        node = nxt * 4
    hdr = end - HEADER_SIZE - 2 * PART_SIZE * n_extra

    def part(at):
        p = {f"{key}_{detail}": ptrs.get(at + PART_SIZE * k + o, -1)
             for k, detail in enumerate(("hi", "lo")) for key, o in (("dl", 0), ("ms", 4), ("an", 8))}
        if p["dl_hi"] < 0 or p["dl_lo"] < 0:
            raise SystemExit("kirby_hat.bin: a hat without a display list")
        return p
    off = part(hdr)
    item = hdr + 2 * PART_SIZE
    off["item"] = item if (item in ptrs or item + PART_SIZE in ptrs) else -1
    off["extras"] = [part(hdr + 4 * PART_SIZE + 2 * PART_SIZE * k) for k in range(n_extra)]
    return off, head


def _blank_comments(text):
    """Comments as spaces (positions kept)."""
    return re.sub(r"//[^\n]*|/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)


def kirby_calls(folder):
    """The character's Character.add_new_action / add_new_action_params for KIRBY / JKIRBY in assembly
    order (main.asm with its includes expanded) -> [(who, 'action' / 'params', scope path, NAME)],
    e.g. ('KIRBY', 'action', ('Cloud', 'KirbyCopy'), 'BladeBeam')."""
    out, seen = [], set()

    def walk(path, stack):
        path = os.path.normpath(path)
        if path in seen or not os.path.exists(path):
            return
        seen.add(path)
        text = _blank_comments(open(path, encoding="utf-8", errors="replace").read())
        for t in re.finditer(r'\binclude\s+"([^"]+)"|\bscope\s+(\w+)\s*:?\s*\{|\{|\}|'
                             r'Character\.add_new_action(_params)?\(\s*(J?KIRBY)\s*,\s*(\w+)', text):
            if t.group(1):
                walk(os.path.join(os.path.dirname(path), t.group(1)), stack)
            elif t.group(4):
                out.append((t.group(4), "params" if t.group(3) else "action", tuple(x for x in stack if x),
                            t.group(5)))
            elif t.group(0) == "}":
                if stack:
                    stack.pop()
            else:
                stack.append(t.group(2))
    walk(os.path.join(folder, "main.asm"), [])
    return out


def count_kirby_actions(calls):
    """kirby_calls() -> {'KIRBY' / 'JKIRBY': {'action': n, 'params': n}}."""
    return {who: {kind: sum(1 for c in calls if c[0] == who and c[1] == kind) for kind in ("action", "params")}
            for who in ("KIRBY", "JKIRBY")}


def kirby_action_names(calls):
    """kirby_calls() -> [(constant path of the action id, NAME)] for Kirby's actions, e.g.
    ('Cloud.KirbyCopy.Action.BladeBeam', 'BladeBeam')."""
    return [(".".join(scope + ("Action", name)), name) for who, kind, scope, name in calls
            if who == "KIRBY" and kind == "action"]


def _asm_text(folder):
    """main.asm with its includes (comments blanked), concatenated."""
    parts, seen = [], set()

    def walk(path):
        path = os.path.normpath(path)
        if path in seen or not os.path.exists(path):
            return
        seen.add(path)
        text = _blank_comments(open(path, encoding="utf-8", errors="replace").read())
        parts.append(text)
        for inc in re.findall(r'\binclude\s+"([^"]+)"', text):
            walk(os.path.join(os.path.dirname(path), inc))
    walk(os.path.join(folder, "main.asm"))
    return "\n".join(parts)


def animation_frames(data, head=None, flags=0):
    """An animation file's length in frames (its longest joint script; like ssb64_model's
    figatree.load), or None if it can't be read."""
    tools = os.path.join(os.path.dirname(SMASHREMIX_PATH), "scripts", "ssb64_model")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    try:
        import figatree as FT
        head = FT.first_pointer_head(data) if head is None else head
        arr, ptrs = FT.pointer_array(data, head)
        rest = dict(rx=0.0, ry=0.0, rz=0.0, tx=0.0, ty=0.0, tz=0.0, sx=1.0, sy=1.0, sz=1.0)
        ends = [FT.play(data, ptrs, sc, rest, anim_joint=bool(flags & FT.FLAG_ANIMJOINT))["frames"]
                for sc in arr if sc is not None]
        return max(ends) + 1 if ends else None
    except Exception:       # (an unreadable file: no check)
        return None


_ARGS = r"\s*([^,()]+?)\s*"


def check_animation_lengths(char_folder, original_path):
    """Kirby hat warnings: a Kirby copy action (add_new_action_params(KIRBY, ...)) whose
    kirby_hat/animations file isn't as long as the animation its timing comes from:
      - with its own moveset (kirby_<m>, kirby_hat/moveset/<m>.bin): the character's animation
        for its own moveset <m> (moveset/<m>.bin), whose timing the copy's moveset follows;
      - else (Kirby's moveset): Kirby's vanilla animation of the base action (Action.KIRBY.X).
    -> [warning strings] (also logged)."""
    text = _asm_text(original_path)
    upper = char_folder.upper()
    anims = {os.path.splitext(f)[0].upper(): os.path.join(original_path, "kirby_hat", "animations", f)
             for f in (os.listdir(os.path.join(original_path, "kirby_hat", "animations"))
                       if os.path.isdir(os.path.join(original_path, "kirby_hat", "animations")) else [])
             if f.lower().endswith(".bin")}
    own_anims = {os.path.splitext(f)[0].upper(): os.path.join(original_path, "animations", f)
                 for f in (os.listdir(os.path.join(original_path, "animations"))
                           if os.path.isdir(os.path.join(original_path, "animations")) else [])
                 if f.lower().endswith(".bin")}
    if not anims:
        return []
    # the character's own actions: moveset label -> its animation file
    own = {}
    for m in re.finditer(r"Character\.edit_action_parameters\(" + ",".join([_ARGS] * 4) + r"[,)]", text):
        who, _, anim, mv = m.groups()
        if who not in ("KIRBY", "JKIRBY"):
            own.setdefault(mv.split(".")[-1], anim)
    for m in re.finditer(r"Character\.add_new_action_params\(" + ",".join([_ARGS] * 5) + r"[,)]", text):
        who, _, _, anim, mv = m.groups()
        if who not in ("KIRBY", "JKIRBY"):
            own.setdefault(mv.split(".")[-1], anim)
    lengths, warnings, done = {}, [], set()

    def length(path):
        if path not in lengths:
            lengths[path] = animation_frames(open(path, "rb").read())
        return lengths[path]

    vanilla = None
    for m in re.finditer(r"Character\.add_new_action_params\(" + ",".join([_ARGS] * 6) + r"\)", text):
        who, name, base, anim, mv, _ = m.groups()
        km = re.fullmatch(rf"File\.{upper}_KIRBY_ANIM_(\w+)", anim)
        if who not in ("KIRBY", "JKIRBY") or not km or km.group(1).upper() not in anims or (name, anim, mv) in done:
            continue
        done.add((name, anim, mv))
        path = anims[km.group(1).upper()]
        n = length(path)
        ref, what = None, None
        mm = re.fullmatch(r"(?:\w+\.)?kirby_(\w+)", mv)
        if mm:                                  # its own moveset: the character's for the same file
            own_anim = own.get(mm.group(1))
            om = re.fullmatch(rf"File\.{upper}_ANIM_(\w+)", own_anim or "")
            if om and om.group(1).upper() in own_anims:
                ref = length(own_anims[om.group(1).upper()])
                what = f"{char_folder}'s animations/{os.path.basename(own_anims[om.group(1).upper()])} " \
                       f"(its moveset {mm.group(1)}, which kirby_{mm.group(1)} follows)"
        elif mv == "-1" and base.startswith("Action.KIRBY."):   # Kirby's own moveset: his animation's length
            try:
                tools = os.path.join(os.path.dirname(SMASHREMIX_PATH), "scripts", "ssb64_model")
                if tools not in sys.path:
                    sys.path.insert(0, tools)
                import motions as MO
                from SSB import N64, SSBtbl
                if vanilla is None:
                    vanilla = (MO.Vanilla(ROM), MO.action_names()[0],
                               SSBtbl.fromROM(N64(open(ROM, "rb").read())))
                van, names, entries = vanilla
                action = names.get(base)
                i = van.param_index(8, action) if action is not None else None
                if i is not None:
                    fid, _, flags = van.params(8)[i]
                    e = entries[fid]
                    ref = animation_frames(e.extract("data"), e.tbl or 0, flags)
                    what = f"Kirby's {base} animation (its moveset)"
            except Exception:
                ref = None
        if n is not None and ref is not None and n != ref:
            w = (f"Kirby hat warning: {char_folder}: Kirby's {name} animation kirby_hat/animations/"
                 f"{os.path.basename(path)} is {n} frames, {what} is {ref}")
            warnings.append(w)
            logger.warning(w)
    return warnings


class KirbyHats:
    def __init__(self):
        self.hats = []          # [{char, file, offsets, size}]
        self.extra_kirby_actions = 0
        self.extra_jkirby_actions = 0
        self.kirby_action_names = []    # [(action id constant, training mode string)], assembly order
        self.counts = {}                # {character folder: count_kirby_actions()}
        self.files = {}                 # {hat owner's folder: its hat file} (shared: kirby_hat_from)

    # ---------------------------------------------------------------- per character
    def process_character(self, char_folder, original_path, output_path):
        """Register kirby_hat/kirby_hat.bin + kirby_hat/animations; count the Kirby actions in its code.
        -> True if it has a hat."""
        calls = kirby_calls(original_path)
        for who in ("KIRBY", "JKIRBY"):
            scopes = sorted({".".join(c[2]) or "(top level)" for c in calls if c[0] == who})
            if len(scopes) > 1:
                # (the counters of new actions are defines of the scope the macros run in: each scope
                # would start over from the character's first id)
                raise SystemExit(f"{char_folder}: its {who} actions are in several scopes ({', '.join(scopes)}): "
                                 "put them all in one scope (e.g. scope KirbyCopy / JKirbyCopy)")
        n = count_kirby_actions(calls)
        self.counts[char_folder] = n
        if any(v for d in n.values() for v in d.values()):
            self.extra_kirby_actions += max(n["KIRBY"].values())
            self.extra_jkirby_actions += max(n["JKIRBY"].values())
            print(f"Kirby actions: {char_folder} adds {n['KIRBY']['action']} (Kirby), "
                  f"{n['JKIRBY']['action']} (J Kirby)")
        self.kirby_action_names += kirby_action_names(calls)
        check_animation_lengths(char_folder, original_path)

        anim_dir = os.path.join(output_path, "kirby_hat", "animations")
        for anim in sorted(os.listdir(anim_dir)) if os.path.isdir(anim_dir) else []:
            if not anim.lower().endswith(".bin"):
                continue
            path = os.path.join(anim_dir, anim)
            data = open(path, "rb").read()
            zeros = 0                       # (like the character's animations: its leading empty words)
            while zeros * 4 + 4 <= len(data) and data[zeros * 4:zeros * 4 + 4] == b"\0\0\0\0":
                zeros += 1
            FileManager.add_file(path=path, name=f"{char_folder}_kirby_anim_{os.path.splitext(anim)[0]}",
                                 internal_file_table_offset=zeros * 4, internal_file_resource_offset=0x3FFFC,
                                 compression_level=0)

        # config.yaml kirby_hat_from: <character folder>: wear that character's hat (one hat file in
        # the ROM, in every sharer's req list; each still gets its own hat id and main.bin pointer)
        path = os.path.join(output_path, "kirby_hat", "kirby_hat.bin")
        owner = char_folder
        config = yaml.safe_load(open(os.path.join(original_path, "config.yaml"), encoding="utf-8")) or {}
        share = config.get("kirby_hat_from")
        if share:
            if os.path.exists(path):
                raise SystemExit(f"{char_folder}: kirby_hat_from: {share}, but it has its own kirby_hat/kirby_hat.bin")
            owner = share
            path = os.path.join(os.path.dirname(os.path.normpath(original_path)), share, "kirby_hat", "kirby_hat.bin")
            if not os.path.exists(path):
                raise SystemExit(f"{char_folder}: kirby_hat_from: {share}, but there is no {path}")
        elif not os.path.exists(path):
            return False
        data = open(path, "rb").read()
        offsets, head = hat_file_offsets(data)
        f = self.files.get(owner)
        if f is None:
            f = self.files[owner] = FileManager.add_file(
                path=path,
                name=f"{owner}_kirby_hat",
                internal_file_table_offset=head,
                internal_file_resource_offset=0x3FFFC,
                compression_level=0,
            )
        # its main file's req list: the hat file last (the processor adds its external pointer last
        # to main.bin's chain: main_slot()); after the template's entries, before append_files'
        req = os.path.join(output_path, "main_reqlist.txt")
        lines = open(req, encoding="utf-8").readlines()
        end = next((i for i, l in enumerate(lines) if l.startswith("END OF")), len(lines))
        if end and not lines[end - 1].endswith("\n"):
            lines[end - 1] += "\n"
        lines.insert(end, f"{f.id:04X} {f.name}\n")
        open(req, "w", encoding="utf-8").writelines(lines)
        self.hats.append(dict(char=char_folder, file=f, offsets=offsets, size=len(data), slot=None))
        print(f"Kirby hat: {char_folder} -> {len(data)} bytes" + (f" ({owner}'s, shared)" if owner != char_folder else "")
              + ", offsets "
              + ", ".join(f"{k} 0x{v:X}" if v >= 0 else f"{k} -" for k, v in offsets.items() if k != "extras")
              + (f", {len(offsets['extras'])} extra hat(s)" if offsets["extras"] else ""))
        return True

    def main_slot(self, char_folder, offset):
        """The processor added the hat file's external pointer to the character's main.bin here."""
        for hat in self.hats:
            if hat["char"] == char_folder:
                hat["slot"] = offset

    # ---------------------------------------------------------------- source
    def patch_src(self):
        self._counters()
        self._grow_action_arrays()
        self._action_strings()
        if not self.hats:
            return
        missing = [h["char"] for h in self.hats if h["slot"] is None]
        if missing:
            raise SystemExit(f"Kirby hats: no main.bin pointer to the hat file for {missing}")
        text = open(KIRBY_HATS_ASM, encoding="utf-8").read()
        m = re.search(r"variable new_hats\((\d+)\)", text)
        if not m:
            raise SystemExit("KirbyHats.asm: no new_hats variable")
        remix_new = int(m.group(1))
        first = 0xF + remix_new                     # hat ids: 0x0E is the last vanilla one
        # our hats' ids, from `first`: each character's hat, then its extra hats
        entries = [(k, n, o) for k, hat in enumerate(self.hats)
                   for n, o in enumerate([hat["offsets"]] + hat["offsets"]["extras"])]
        text = text.replace(m.group(0), f"variable new_hats({remix_new + len(entries)})", 1)

        # hat file lookup: by file index, through a table (Remix's two files, then ours)
        old = re.search(r"\n([ \t]*)li\s+t5, 0x0001D810[^\n]*\n\s*bnezl\s+t7, pc\(\) \+ 8[^\n]*\n"
                        r"\s*addiu\s+t5, t5, 0x0004[^\n]*\n", text)
        if not old:
            raise SystemExit("KirbyHats.asm: hat file pointer lookup not found (Remix changed it?)")
        ind = old.group(1)
        text = text.replace(old.group(0), "\n" + "\n".join(ind + x for x in (
            "sll     t7, t7, 0x0002                 // t7 = file index * 4 (extra: hat file lookup tables)",
            "li      t5, hat_file_owners            // t5 = 0: Kirby's character file, else the owner's main file pointer",
            "addu    t5, t5, t7                     // ~",
            "lw      t5, 0x0000(t5)                 // ~",
            "bnezl   t5, pc() + 8                   // an extra character's hat: in its main file",
            "lw      t3, 0x0000(t5)                 // t3 = the character's main file",
            "li      t5, hat_file_slots             // t5 = offsets of the hat file pointers in those files",
            "addu    t5, t5, t7                     // ~",
            "lw      t5, 0x0000(t5)                 // t5 = offset to this hat file's pointer",
        )) + "\n", 1)

        # gun swap (model part on joint 0x11) for extra hats with an item
        lb = re.search(r"\n([ \t]*)_check_hat_id:\n\s*lb\s+t2, 0x0980\(t0\)[^\n]*\n", text)
        gun_end = re.search(r"\n([ \t]*)_gun_end:\n", text)
        if not lb or not gun_end:
            raise SystemExit("KirbyHats.asm: gun swap code not found (Remix changed it?)")
        ind = lb.group(1)
        text = text.replace(lb.group(0), lb.group(0) + "\n".join(ind + x for x in (
            f"slti    t6, t2, 0x{first:02X}                // extra: hats from 0x{first:02X} have their own item",
            "beqz    t6, _extra_item                // ~",
            "nop",
        )) + "\n", 1)
        gun_end = re.search(r"\n([ \t]*)_gun_end:\n", text)
        ind = gun_end.group(1)
        block = [
            "b       _gun_end                       // extra: (not an extra hat)",
            "nop",
            "_extra_item:",
            f"addiu   t2, t2, -0x{first:02X}               // t2 = extra hat index",
            "sll     t2, t2, 0x0002                 // t2 = index * 4",
            "li      t7, extra_hat_items            // t7 = item part pair offsets (-1: none)",
            "addu    t7, t7, t2                     // ~",
            "lw      t7, 0x0000(t7)                 // t7 = its item's offset in the hat file",
            "bltz    t7, _gun_end                   // no item: Kirby's own",
            "nop",
            "li      t6, extra_hat_owners           // t6 = the hats' characters' main file pointers (by hat id)",
            "addu    t6, t6, t2                     // ~",
            "lw      t6, 0x0000(t6)                 // ~",
            "lw      t6, 0x0000(t6)                 // t6 = the character's main file",
            "li      t0, extra_hat_slots            // t0 = their hat file pointers' offsets (by hat id)",
            "addu    t0, t0, t2                     // ~",
            "lw      t0, 0x0000(t0)                 // t0 = offset of its pointer in that file",
            "addu    t6, t6, t0                     // ~",
            "lw      t6, 0x0000(t6)                 // t6 = the hat file",
            "addu    v1, t6, t7                     // v1 = its item's {hi, lo} model part pair",
        ]
        text = text[:gun_end.start()] + "\n" + "\n".join(ind + x for x in block) + text[gun_end.start():]

        # the hats, their tables, Kirby's inhale table
        lines = ["", "    // smashremix-extra: extra characters' hats (KirbyHats.<NAME>_HAT; extra hats, which",
                 "    // their Kirby movesets show by setting joint 6's model part to the id: <NAME>_HAT_<n>)"]
        for i, (k, n, o) in enumerate(entries):
            hat = self.hats[k]
            name = hat["char"].upper()
            hat_id = first + i
            lines += [
                f"    // {hat['char']} {'hat' if n == 0 else f'extra hat {n}'}: 0x{hat_id:02X}",
                f"    constant {name}_HAT{'' if n == 0 else f'_{n}'}(0x{hat_id:02X})",
                f"    add_hat(Character.kirby_hat_id.NONE, 0x{o['dl_hi']:X}, 0x{o['ms_hi']:X}, 0x{o['an_hi']:X}, "
                f"0x{o['dl_lo']:X}, 0x{o['ms_lo']:X}, 0x{o['an_lo']:X}, {2 + k})",
            ]
            if n == 0:
                lines += [
                    f"    Character.table_patch_start(kirby_inhale_struct, 0x0, Character.id.{name}, 0xC)",
                    f"    dh Character.id.{name}                // copy power",
                    f"    dh 0x{hat_id:02X}                  // hat",
                    "    OS.patch_end()",
                ]
        lines += [
            "",
            "    OS.align(4)",
            "    // The hat files' pointers, by file index: in Kirby's character file (Remix's), in the",
            "    // characters' main files (ours: owner = the address of the character's main file pointer)",
            "    hat_file_owners:",
            "    dw 0, 0" + "".join(f", Character.{h['char'].upper()}_file_1_ptr" for h in self.hats),
            "    hat_file_slots:",
            "    dw 0x1D810, 0x1D814" + "".join(f", 0x{h['slot']:X}" for h in self.hats),
            "    // our hats' (by hat id) item ({hi, lo} model part pair for joint 0x11) offsets in their hat file",
            "    extra_hat_items:",
            "    dw " + ", ".join(f"0x{self.hats[k]['offsets']['item']:X}" if self.hats[k]["offsets"]["item"] >= 0
                                  else "-1" for k, _, _ in entries),
            "    // our hats' (by hat id) characters' main file pointers, hat file pointer offsets in those",
            "    extra_hat_owners:",
            "    dw " + ", ".join(f"Character.{self.hats[k]['char'].upper()}_file_1_ptr" for k, _, _ in entries),
            "    extra_hat_slots:",
            "    dw " + ", ".join(f"0x{self.hats[k]['slot']:X}" for k, _, _ in entries),
        ]
        last_add = list(re.finditer(r"\n\s*add_hat\([^\n]*\n", text))[-1]
        text = text[:last_add.end()] + "\n".join(lines) + "\n" + text[last_add.end():]
        open(KIRBY_HATS_ASM, "w", encoding="utf-8").write(text)

        logger.info(f"Kirby hats: {[h['char'] for h in self.hats]} from id 0x{first:02X}")

    def _counters(self):
        """Kirby's / J Kirby's new action / parameter counters before each character's code.

        Character.add_new_action(_params) count in defines of the scope they run in (bass keeps a
        macro's 'global evaluate' there): Kirby.asm's in scope Kirby, each character's in its
        KirbyCopy scope. Each character starts from the top level's: set before its include to
        Remix's count plus the characters' before it (in include order), or its Kirby actions would
        take the ids of Remix's / the other characters'."""
        text = open("main.asm", encoding="utf-8").read()
        anchor = re.search(r'include "src/JKirby/JKirby.asm"[^\n]*\n', text)
        if not anchor:
            raise SystemExit('main.asm: no include "src/JKirby/JKirby.asm"')
        text = (text[:anchor.end()]
                + "// smashremix-extra: helpers for the characters' Kirby copy abilities (smashremix_overwrite)\n"
                + 'include "src/KirbyCopy.asm"\n' + text[anchor.end():])
        totals = {(who, kind): 0 for who in ("KIRBY", "JKIRBY") for kind in ("action", "params")}
        base = {("KIRBY", "action"): "Kirby.KIRBY_new_actions", ("KIRBY", "params"): "Kirby.KIRBY_new_params",
                ("JKIRBY", "action"): "JKirby.JKIRBY_new_actions", ("JKIRBY", "params"): "JKirby.JKIRBY_new_params"}
        out, pos = [], 0
        for m in re.finditer(r'^[ \t]*include "build/extra_characters/([^/"]+)/main\.asm"[^\n]*\n', text, re.M):
            n = self.counts.get(m.group(1))
            out.append(text[pos:m.start()])
            out.append(f"// smashremix-extra: Kirby's / J Kirby's new action counters for {m.group(1)}'s Kirby actions\n")
            for (who, kind), b in base.items():
                define = f"{who}_new_{'actions' if kind == 'action' else 'params'}"
                out.append(f"global evaluate {define}({{{b}}} + {totals[who, kind]})\n")
            out.append(m.group(0))
            pos = m.end()
            for key in totals:
                totals[key] += (n or {}).get(key[0], {}).get(key[1], 0)
        out.append(text[pos:])
        open("main.asm", "w", encoding="utf-8").write("".join(out))

    def _action_strings(self):
        """Training mode strings of the extra Kirby actions: Kirby's action string table (J Kirby
        uses it too) gets room after its last entry, filled by action id (generated file, assembled
        after the characters: their action ids are known there)."""
        if not self.kirby_action_names:
            return
        text = open("src/Action.asm", encoding="utf-8").read()
        k = text.index("    scope KIRBY {")
        t = text.index("action_string_table:", k)
        end = text.index("\n    }", t)
        line_start = text.rindex("\n", 0, t) + 1
        indent = text[line_start:t]
        n = len(self.kirby_action_names)
        text = (text[:t] + "action_string_table:\n" + indent + "constant TABLE_ORIGIN(origin())" + text[t + len("action_string_table:"):end]
                + f"\n{indent}// smashremix-extra: extra characters' Kirby actions (filled by build/kirby_action_strings.asm)"
                + f"\n{indent}constant TABLE_END(origin())"
                + f"\n{indent}fill {4 * n}" + text[end:])
        open("src/Action.asm", "w", encoding="utf-8").write(text)
        lines = ["// Training mode strings of the extra characters' Kirby actions (src/KirbyHats... kirby_hat.py)",
                 "scope ExtraKirbyActionStrings {"]
        for i, (const, name) in enumerate(self.kirby_action_names):
            lines.append(f'    string_{i}:; String.insert("{name}")')
        lines.append("    OS.align(4)")
        lines.append("    pushvar origin, base")
        for i, (const, name) in enumerate(self.kirby_action_names):
            lines += [f"    if (({const} - 0xDC) * 4) + Action.KIRBY.TABLE_ORIGIN >= Action.KIRBY.TABLE_END + {4 * n} {{",
                      f'        error "Kirby action {name}: past the room for extra Kirby action strings"',
                      "    }",
                      f"    origin Action.KIRBY.TABLE_ORIGIN + (({const} - 0xDC) * 4)",
                      f"    dw string_{i}"]
        lines += ["    pullvar base, origin", "}"]
        os.makedirs("build", exist_ok=True)
        open("build/kirby_action_strings.asm", "w", encoding="utf-8").write("\n".join(lines) + "\n")
        lineinfile.add_line_to_file(filepath="main.asm", line='include "build/kirby_action_strings.asm"',
                                    inserter=lineinfile.BeforeFirst(r"^// RANDOM"))

    def _grow_action_arrays(self):
        if self.extra_kirby_actions:
            text = open("src/Character.asm", encoding="utf-8").read()
            m = re.search(r"extend_kirby_arrays\((\d+)\)", text)
            if not m:
                raise SystemExit("Character.asm: extend_kirby_arrays(...) not found")
            text = text.replace(m.group(0), f"extend_kirby_arrays({int(m.group(1)) + self.extra_kirby_actions})", 1)
            open("src/Character.asm", "w", encoding="utf-8").write(text)
        if self.extra_jkirby_actions:
            text = open("src/Character.asm", encoding="utf-8").read()
            m = re.search(r"(define_character\(JKIRBY,(?:[^,]*,){11}\s*)(0x[0-9A-Fa-f]+|\d+)(,)", text)
            if not m:
                raise SystemExit("Character.asm: J Kirby's define_character not found")
            n = int(m.group(2), 0) + self.extra_jkirby_actions
            text = text.replace(m.group(0), f"{m.group(1)}0x{n:X}{m.group(3)}", 1)
            open("src/Character.asm", "w", encoding="utf-8").write(text)


kirby_hats = KirbyHats()
