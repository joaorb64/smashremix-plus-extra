#!/usr/bin/env python3
"""
ge_dl_edit.py - edit the display lists of a GoldEditor model .bin as text.

    ge_dl_edit.py export MODEL.bin [--footer HB.bin] [--head 0x22C] [-o MODEL.dl.txt]
    ge_dl_edit.py import MODEL.dl.txt

export writes every display list the model draws (found from the hitbox
footer's DObjDesc list, <name>_hitbox.bin next to the model by default) as
one command per line - gbi.h macros where possible, raw hex otherwise.
Edit it freely: add, remove, reorder, change lines.  import rebuilds the .bin
with a fresh relocation chain (so any number of lines can be added or
removed), repoints the hitbox footer, updates the chain head in a config.yaml
next to the model if it moved, and re-exports the text so it matches again.
"""
import argparse
import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from smashremix_extra import ge_bin, gbi_text  # noqa: E402

HEADER = """\
# Display lists of {model}  (GoldEditor model)
#
# Edit freely - add, remove, reorder or change lines - then rebuild the .bin:
#     python3 appender/scripts/ge_dl_edit.py import {txt}
#
# Each line is a gbi.h macro, e.g.
#     gsDPSetRenderMode(G_RM_AA_ZB_OPA_SURF, G_RM_AA_ZB_OPA_SURF2)
# or a raw command "XXXXXXXX XXXXXXXX".  A second word like "@15F0" is a
# pointer to offset 0x15F0 of the .bin (texture, palette, vertices...) - copy
# those lines as they are.  Anything after '#' is a comment.  Every "dl" block
# must end with gsSPEndDisplayList().
#
# Leave the four lines below alone.  If the .bin changes (GoldEditor
# re-export), export again - import refuses a stale file.
"""

LIST_NAME = {0: "opaque", 1: "translucent"}


def _sha1(b):
    return hashlib.sha1(b).hexdigest()


def _default_footer(model):
    hb = model.with_name(model.stem + "_hitbox.bin")
    return hb if hb.exists() else None


def _config_head(model):
    """Chain head from config.yaml `files:` next to the model, if listed."""
    cfg = model.parent / "config.yaml"
    if cfg.exists():
        m = re.search(r'\[\s*' + re.escape(model.stem) + r'\s*,\s*"([0-9A-Fa-f]+)"',
                      cfg.read_text())
        if m:
            return int(m.group(1), 16)
    return None


def _roots(d, footer):
    if footer:
        return [ge_bin.footer_roots(Path(footer).read_bytes()).dobjdesc]
    return [o.start + 0x10 for o in ge_bin.find_objects(d)]


def export(model, footer, head, out):
    d = model.read_bytes()
    tgt = {n.field: n.target for n in ge_bin.walk_chain(d, head)}
    rel = lambda p: Path(p).name                                  # noqa: E731
    lines = [HEADER.format(model=model.name, txt=out),
             f"model  {rel(model)}",
             f"footer {rel(footer) if footer else '-'}",
             f"head   0x{head:X}",
             f"sha1   {_sha1(d)}", ""]
    for dl in ge_bin.find_display_lists(d, head, _roots(d, footer)):
        kind = LIST_NAME.get(dl.list_id, f"list {dl.list_id}") \
            if dl.list_id is not None else "plain"
        lines.append(f"dl 0x{dl.start:X}    # bone {dl.entry}, {kind}")
        for off, w0, w1 in dl.cmds:
            if off + 4 in tgt:
                src = f"{w0:08X} @{tgt[off + 4]:X}"
                note = gbi_text.describe(w0, w1)
            else:
                src = gbi_text.decode(w0, w1)
                note = ""
                if src is None:
                    src = f"{w0:08X} {w1:08X}"
                    note = gbi_text.describe(w0, w1)
            if w0 == 0xE200001C:
                note = gbi_text.rm_note(w1)
            lines.append(f"    {src:<40}  # {note}" if note else f"    {src}")
        lines.append("")
    out.write_text("\n".join(lines))
    return out


_HEX = re.compile(r"^([0-9A-Fa-f]{8})\s+(?:([0-9A-Fa-f]{8})|@([0-9A-Fa-f]+))$")


def _parse(txt):
    meta, blocks, cur = {}, [], None
    for no, raw in enumerate(txt.read_text().splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        where = f"{txt.name}:{no}"
        key = line.split()[0]
        if key in ("model", "footer", "head", "sha1") and cur is None:
            meta[key] = line.split(None, 1)[1].strip()
            continue
        if key == "dl":
            cur = {"start": int(line.split()[1], 16), "data": bytearray(),
                   "ptrs": [], "line": where}
            blocks.append(cur)
            continue
        if cur is None:
            sys.exit(f"{where}: command outside a 'dl' block")
        m = _HEX.match(line)
        try:
            if m:
                w0 = int(m.group(1), 16)
                if m.group(3):
                    cur["ptrs"].append((len(cur["data"]) + 4, int(m.group(3), 16)))
                    w1 = 0
                else:
                    w1 = int(m.group(2), 16)
            else:
                w0, w1 = gbi_text.assemble(line)
        except ValueError as e:
            sys.exit(f"{where}: {e}")
        cur["data"] += w0.to_bytes(4, "big") + w1.to_bytes(4, "big")
    for b in blocks:
        if not b["data"] or b["data"][-8] != 0xDF:
            sys.exit(f"{b['line']}: dl 0x{b['start']:X} must end with "
                     f"gsSPEndDisplayList()")
    return meta, blocks


def do_import(txt):
    meta, blocks = _parse(txt)
    model = txt.parent / meta["model"]
    footer = None if meta.get("footer", "-") == "-" else txt.parent / meta["footer"]
    head = int(meta["head"], 16)
    d = model.read_bytes()
    if _sha1(d) != meta.get("sha1"):
        sys.exit(f"{model.name} changed since this text was exported "
                 f"(GoldEditor re-export?) - run export again and redo the edits")

    tgt = {n.field: n.target for n in ge_bin.walk_chain(d, head)}
    old = {dl.start: dl for dl in ge_bin.find_display_lists(d, head, _roots(d, footer))}
    splices = []
    for b in blocks:
        dl = old.get(b["start"])
        if dl is None:
            sys.exit(f"{b['line']}: no display list at 0x{b['start']:X}")
        n_old = len(dl.cmds) * 8
        # unchanged = same bytes outside pointer words + same pointers
        cur = bytearray(d[dl.start:dl.start + n_old])
        cur_ptrs = []
        for k in range(4, n_old, 8):
            if dl.start + k in tgt:
                cur_ptrs.append((k, tgt[dl.start + k]))
                cur[k:k + 4] = bytes(4)
        if bytes(b["data"]) == bytes(cur) and b["ptrs"] == cur_ptrs:
            continue
        splices.append(ge_bin.Splice(dl.start, n_old, bytes(b["data"]),
                                     tuple(b["ptrs"]), True))
    if not splices:
        print("no changes")
        return
    try:
        res = ge_bin.splice(d, head, splices)
    except ValueError as e:
        sys.exit(f"can't rebuild {model.name}: {e}")
    model.write_bytes(res.data)
    print("\n".join(res.report))
    print(f"wrote {model}")

    if footer:
        fb = bytearray(footer.read_bytes())
        fr = ge_bin.footer_roots(bytes(fb))
        for node, o in ((fr.dd_node, fr.dobjdesc), (fr.pm_node, fr.pmobjsubs)):
            if node is not None:
                ge_bin.w16(fb, node + 2, res.remap(o) // 4)
        footer.write_bytes(fb)
        print(f"repointed {footer}")

    if res.new_head != head:
        cfg = model.parent / "config.yaml"
        pat = re.compile(r'(\[\s*' + re.escape(model.stem) + r'\s*,\s*")([0-9A-Fa-f]+)(")')
        if cfg.exists() and pat.search(cfg.read_text()):
            cfg.write_text(pat.sub(lambda m: f"{m.group(1)}{res.new_head:04X}{m.group(3)}",
                                   cfg.read_text(), count=1))
            print(f"chain head 0x{head:X} -> 0x{res.new_head:X}: updated {cfg}")
        else:
            print(f"NOTE: chain head moved 0x{head:X} -> 0x{res.new_head:X} - "
                  f"update the model's table offset (config.yaml files: col 1)")

    export(model, footer, res.new_head, txt)
    print(f"re-exported {txt}")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export", help="model .bin -> editable .dl.txt")
    e.add_argument("model")
    e.add_argument("--footer", help="hitbox .bin (default: <name>_hitbox.bin)")
    e.add_argument("--head", help="chain head, hex (default: config.yaml, else auto)")
    e.add_argument("-o", "--out", help="default: <name>.dl.txt next to the model")
    i = sub.add_parser("import", help=".dl.txt -> rebuilt model .bin")
    i.add_argument("txt")
    a = p.parse_args()

    if a.cmd == "import":
        do_import(Path(a.txt))
        return
    model = Path(a.model)
    d = model.read_bytes()
    footer = Path(a.footer) if a.footer else _default_footer(model)
    head = int(a.head, 16) if a.head else (_config_head(model) or ge_bin.find_chain_head(d))
    if head is None:
        sys.exit("could not find the chain head - pass --head")
    out = Path(a.out) if a.out else model.with_name(model.stem + ".dl.txt")
    print(f"wrote {export(model, footer, head, out)}")


if __name__ == "__main__":
    main()
