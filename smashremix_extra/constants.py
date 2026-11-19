import os
import re
from dataclasses import dataclass

# Resolved relative to this file rather than CWD, since the smashremix
# submodule ships alongside this package and callers may run the appender
# from a different working directory (e.g. a content repo that pulls this
# in as a submodule, with its own extra_characters/build/src at CWD).
SMASHREMIX_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "smashremix")

APPENDER_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_path(path):
    """A resource path (fonts, templates...) that works from any working directory.

    Paths that exist as given are kept. Otherwise "appender/..." (written for builds run from
    a content repo that has this repo as its "appender" submodule) and other relative paths
    are looked up in this repo's folder, whatever it's called (e.g. a downloaded zip)."""
    if not path or os.path.isabs(path) or os.path.exists(path):
        return path
    parts = re.split(r"[\\/]", path)
    rest = parts[1:] if parts[0] == "appender" else parts
    candidate = os.path.join(APPENDER_PATH, *rest)
    return candidate if os.path.exists(candidate) else path

VARIANT_TYPES = ["dl", "omega", "remix", "remix2", "remix3", "remix4", "remix5", "remix6"]

PRIMARY_MOVESETS = {
    "MARIO": "0x0CA",
    "METAL": "0x0CD",
    "FOX": "0x0D0",
    "DONKEY": "0x0D4",
    "SAMUS": "0x0D8",
    "LUIGI": "0x0DC",
    "LINK": "0x0E0",
    "KIRBY": "0x0E4",
    "JIGGLYPUFF": "0x0E8",
    "CAPTAIN": "0x0EB",
    "NESS": "0x0EE",
    "PIKACHU": "0x0F2",
    "YOSHI": "0x0F6",
    "BOSS": "0x0F9"
}

SHIELD_POSES = {
    "MARIO": "0x012A",
    "METAL": "0x012A",
    "FOX": "0x013A",
    "DONKEY": "0x013E",
    "SAMUS": "0x0142",
    "LUIGI": "0x012A",
    "LINK": "0x0147",
    "KIRBY": "0x0149",
    "JIGGLYPUFF": "0x014B",
    "CAPTAIN": "0x014E",
    "NESS": "0x0151",
    "PIKACHU": "0x0157",
    "YOSHI": "0x0154"
}

TWELVECB_DEFEAT = {
    "MARIO": "0x222",
    "METAL": "0x222",
    "FOX": "0x2B1",
    "DONKEY": "0x34F",
    "SAMUS": "0x3E8",
    "LUIGI": "0x222",
    "LINK": "0x48A",
    "KIRBY": "0x51D",
    "JIGGLYPUFF": "0x51D",
    "CAPTAIN": "0x617",
    "NESS": "0x6AF",
    "PIKACHU": "0x80E",
    "YOSHI": "0x745",
    "BOSS": "0"
}

SP_DUO_POSES = {
    "MARIO": "0x170",
    "METAL": "0x171",
    "FOX": "File.FOX_DUO_POSE",
    "DONKEY": "File.DK_DUO_POSE",
    "SAMUS": "File.SAMUS_DUO_POSE",
    "LUIGI": "0x17B",
    "LINK": "File.LINK_DUO_POSE",
    "KIRBY": "File.KIRBY_DUO_POSE",
    "JIGGLYPUFF": "0x51D",
    "CAPTAIN": "0x617",
    "NESS": "File.NESS_DUO_POSE",
    "PIKACHU": "File.PIKA_DUO_POSE",
    "YOSHI": "0x1C8",
    "BOSS": "0"
}

SP_TEAM_POSES = {
    "MARIO": "0x222",
    "METAL": "0x222",
    "FOX": "0x2B1",
    "DONKEY": "0x34F",
    "SAMUS": "0x3E8",
    "LUIGI": "0x222",
    "LINK": "0x48A",
    "KIRBY": "0x51D",
    "JIGGLYPUFF": "0x51D",
    "CAPTAIN": "0x617",
    "NESS": "0x6AF",
    "PIKACHU": "0x80E",
    "YOSHI": "0x745",
    "BOSS": "0"
}

COMMAND_SIZES = {
    "0C": 5,
    "0D": 5,
    "10": 5,
    "30": 2,
    "34": 2,
    "7C": 4,
    "88": 2,
    "90": 2,
    "98": 4,
    "9A": 4,
    "9C": 4,
    "B8": 2,
}


@dataclass
class ExtraFile:
    filename: str
    character_list_id: int
    InternalFileTableOffsetBytes: int
    InternalFileResourceOffsetBytes: int
    game_file_id: int = -1
