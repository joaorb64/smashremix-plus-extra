"""
Dump every stage attribute this toolchain understands to a single YAML file -
`build/extra_stages/<S>/stage_attributes.yaml` - for debugging. It is the same
shape a stage's `config.yaml` uses, so a block can be copied straight back in.

Read-only: decodes the *built* stage.bin + header.bin and writes the YAML.
"""
from __future__ import annotations

import yaml

from smashremix_extra.stage import collision, ground

_MAPOBJ_REBIRTH = 0x20


def _mapobjs(geo):
    out = []
    for kind, x, y in geo.mapobjs:
        out.append({"kind": f"0x{kind:02X}",
                    "name": collision.MAPOBJ_KIND.get(kind, "?"),
                    "x": x, "y": y})
    return out


def dump(stage_path, header_path, chain_head, groupdata_off):
    """-> YAML string with collision, map objects, rebirth, and all MPGroundData
    fields."""
    sb = open(stage_path, "rb").read()
    hb = open(header_path, "rb").read()
    geo = collision.decode(sb, chain_head)

    doc = {"collision_groups": geo.group_count,
           "collision": collision.to_spec(geo),
           "map_objects": _mapobjs(geo)}

    for kind, x, y in geo.mapobjs:
        if kind == _MAPOBJ_REBIRTH:
            doc["rebirth"] = [x, y]
            break

    doc.update(ground.read(hb, groupdata_off))
    return yaml.safe_dump(doc, sort_keys=False, default_flow_style=None,
                          width=100)


def dump_to(out_path, stage_path, header_path, chain_head, groupdata_off):
    text = dump(stage_path, header_path, chain_head, groupdata_off)
    open(out_path, "w", encoding="utf-8").write(text)
    return out_path
