"""Standard MIDI file (.mid) -> N64 compressed sequence (.bin) converter.

The output is libultra's compressed MIDI format (ALCSeq), the same format as
every music .bin used by Smash 64 / Smash Remix:

  0x00  u32[16]  offset of each track (0 = unused)
  0x40  u32      division (ticks per quarter note)
  0x44  track data

Track data is MIDI with these differences (see libultra cseq.c):
  - no note-off events: note-on is followed by a varlen note duration
  - meta events have no length byte (FF 51 tt tt tt = tempo, FF 2F = end)
  - FF 2E nn FF               loop start
  - FF 2D cc cc oo oo oo oo   loop end; cc = loop count (FF = forever), o =
                              offset from the end of this event back to the
                              byte after the matching loop start
  - FE hi lo ll               back-reference: replay ll raw bytes found
                              (hi<<8|lo) bytes before the FE; a literal 0xFE
                              byte is written FE FE

Usage (standalone):
  python3 -m smashremix_extra.audio.midi_to_bin song.mid song.bin [song.yaml]
      [--rom original.z64 --midi-asm src/midi.asm]

Game rules (the sequence player crashes otherwise, see bank.py):
  - every channel needs a program change before its first note, to an
    existing instrument (0 is an empty slot)
  - every note's key/velocity must be covered by a sample of its instrument
  Doubled/overlapping notes (same channel + key) are fixed automatically.

Instruments come from the MIDI file's program changes: Smash Remix
instrument ids (the Smash Remix SF2 numbers its presets the same way).

Optional key in the song's yaml (extra_music/<game>/<song>.yaml):

  loop: auto          # auto (default) | false | {start: tick, end: tick}
"""

import os
import struct
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

try:
    from smashremix_extra.logger import logger
except ImportError:  # standalone use
    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger = logging.getLogger("midi_to_bin")



# Per-channel controllers re-sent after the loop start when they change
# inside the loop, so every loop iteration starts from the same state.
SNAPSHOT_CCS = (1, 7, 10, 11, 64, 91, 93)


# --------------------------------------------------------------------------
# Standard MIDI file parsing
# --------------------------------------------------------------------------

@dataclass
class Note:
    tick: int
    channel: int
    key: int
    velocity: int
    duration: int


@dataclass
class SourceTrack:
    index: int
    name: str = ""
    notes: List[Note] = field(default_factory=list)
    # (tick, status, data bytes) of non-note channel events
    events: List[Tuple[int, int, bytes]] = field(default_factory=list)
    channels: set = field(default_factory=set)


@dataclass
class Song:
    division: int
    tracks: List[SourceTrack]
    tempos: List[Tuple[int, int]]          # (tick, usec per quarter)
    time_sig: Tuple[int, int]              # (numerator, denominator)
    markers: List[Tuple[int, str]]         # (tick, text)
    cc111: List[int]                       # ticks of CC 111 (loop start)


def _read_varlen(data: bytes, pos: int) -> Tuple[int, int]:
    value = 0
    while True:
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, pos


def parse_midi(data: bytes) -> Song:
    if data[:4] != b"MThd":
        raise ValueError("not a standard MIDI file")
    header_len = struct.unpack(">I", data[4:8])[0]
    fmt, ntracks, division = struct.unpack(">HHH", data[8:14])
    if division & 0x8000:
        raise ValueError("SMPTE time division is not supported")
    if fmt == 2:
        raise ValueError("MIDI format 2 is not supported")

    pos = 8 + header_len
    tracks: List[SourceTrack] = []
    tempos: Dict[int, int] = {}
    time_sig: Optional[Tuple[int, int]] = None
    markers: List[Tuple[int, str]] = []
    cc111: List[int] = []

    for index in range(ntracks):
        if data[pos:pos + 4] != b"MTrk":
            raise ValueError(f"track {index}: missing MTrk chunk")
        length = struct.unpack(">I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + length]
        pos += 8 + length

        track = SourceTrack(index)
        open_notes: Dict[Tuple[int, int], List[Tuple[int, int]]] = defaultdict(list)
        tick = 0
        i = 0
        status = 0
        while i < len(body):
            delta, i = _read_varlen(body, i)
            tick += delta
            byte = body[i]
            if byte == 0xFF:
                meta = body[i + 1]
                size, i = _read_varlen(body, i + 2)
                payload = body[i:i + size]
                i += size
                if meta == 0x51 and size == 3:
                    tempos[tick] = (payload[0] << 16) | (payload[1] << 8) | payload[2]
                elif meta == 0x58 and size >= 2 and time_sig is None:
                    time_sig = (payload[0], 1 << payload[1])
                elif meta == 0x03 and not track.name:
                    track.name = payload.decode("latin-1").strip()
                elif meta in (0x01, 0x06):
                    markers.append((tick, payload.decode("latin-1").strip()))
                elif meta == 0x2F:
                    break
                continue
            if byte in (0xF0, 0xF7):
                size, i = _read_varlen(body, i + 1)
                i += size
                continue
            if byte & 0x80:
                status = byte
                i += 1
            elif not status:
                raise ValueError(f"track {index}: running status without status")
            kind = status & 0xF0
            channel = status & 0x0F
            nbytes = 1 if kind in (0xC0, 0xD0) else 2
            args = body[i:i + nbytes]
            i += nbytes
            track.channels.add(channel)

            if kind == 0x90 and args[1] > 0:
                open_notes[(channel, args[0])].append((tick, args[1]))
            elif kind == 0x80 or kind == 0x90:
                pending = open_notes.get((channel, args[0]))
                if pending:
                    start, velocity = pending.pop(0)
                    track.notes.append(Note(start, channel, args[0], velocity,
                                            max(1, tick - start)))
            else:
                if kind == 0xB0 and args[0] == 111:
                    cc111.append(tick)
                track.events.append((tick, status, bytes(args)))

        for (channel, key), pending in open_notes.items():
            for start, velocity in pending:
                track.notes.append(Note(start, channel, key, velocity,
                                        max(1, tick - start)))
        track.notes.sort(key=lambda n: n.tick)
        tracks.append(track)

    if not tempos:
        tempos[0] = 500000
    return Song(division, tracks, sorted(tempos.items()),
                time_sig or (4, 4), markers, sorted(cc111))


# --------------------------------------------------------------------------
# Channel / instrument assignment
# --------------------------------------------------------------------------

@dataclass
class Part:
    """One output channel."""
    channel: int
    notes: List[Note] = field(default_factory=list)
    events: List[Tuple[int, int, bytes]] = field(default_factory=list)
    label: str = ""


def assign_channels(song: Song) -> List[Part]:
    """Map source (track, channel) pairs to N64 channels.

    Channels are kept as in the file, except when several tracks share a
    channel: then each track gets its own channel (with its own events).
    """

    groups: List[Tuple[SourceTrack, int]] = []
    for track in song.tracks:
        for channel in sorted({n.channel for n in track.notes}):
            groups.append((track, channel))

    owners: Dict[int, set] = defaultdict(set)
    for track, channel in groups:
        owners[channel].add(track.index)
    shared = any(len(t) > 1 for t in owners.values())

    if not shared:
        by_channel: Dict[int, Part] = {}
        for track in song.tracks:
            for channel in track.channels:
                by_channel.setdefault(channel, Part(channel, label=f"ch{channel + 1}"))
            for note in track.notes:
                by_channel[note.channel].notes.append(note)
            for event in track.events:
                by_channel[event[1] & 0x0F].events.append(event)
        return [by_channel[c] for c in sorted(by_channel)]

    if len(groups) > 16:
        raise ValueError(f"{len(groups)} instrument parts, the N64 has 16 channels")
    parts = []
    for out_channel, (track, channel) in enumerate(groups):
        label = track.name or f"track {track.index}"
        part = Part(out_channel, label=label)
        parts.append(part)
        for note in track.notes:
            if note.channel == channel:
                part.notes.append(Note(note.tick, out_channel, note.key,
                                       note.velocity, note.duration))
        for tick, status, args in track.events:
            kind = status & 0xF0
            if (status & 0x0F) != channel:
                continue
            part.events.append((tick, kind | out_channel, args))
        part.notes.sort(key=lambda n: n.tick)
        part.events.sort(key=lambda e: e[0])
    return parts


# --------------------------------------------------------------------------
# Game rules
# --------------------------------------------------------------------------

class MidiConversionError(Exception):
    """The MIDI breaks a rule of the game's sequence player (would crash)."""


def fix_overlaps(parts: List[Part]) -> int:
    """Remove doubled/overlapping notes (same channel and key still sounding).

    Same-tick duplicates are merged (loudest velocity, longest duration);
    a note re-triggered while sounding is cut where the new one starts.
    """
    fixed = 0
    for part in parts:
        result: List[Note] = []
        sounding: Dict[int, Note] = {}
        for note in sorted(part.notes, key=lambda n: (n.tick, n.key)):
            prev = sounding.get(note.key)
            if prev and note.tick < prev.tick + prev.duration:
                fixed += 1
                if note.tick == prev.tick:
                    prev.velocity = max(prev.velocity, note.velocity)
                    prev.duration = max(prev.duration, note.duration)
                    continue
                prev.duration = note.tick - prev.tick
            sounding[note.key] = note
            result.append(note)
        part.notes = result
    return fixed


def validate(parts: List[Part], bank) -> None:
    """Check every note against the instrument bank, like the game will.

    The player crashes (TLB load in __n_lookupSoundQuick) on a note when its
    channel has no instrument (no program change yet, or an empty slot such
    as 0) or no sample of the instrument covers the note's key/velocity.
    """
    errors: List[str] = []
    for part in parts:
        timeline = sorted(
            [(t, 0, a[0]) for t, s, a in part.events if (s & 0xF0) == 0xC0]
            + [(n.tick, 1, n) for n in part.notes],
            key=lambda e: (e[0], e[1]))
        program = None
        bad_programs, bad_keys, no_program = set(), defaultdict(set), 0
        for _, kind, value in timeline:
            if kind == 0:
                program = value
                if not bank.is_valid(program):
                    bad_programs.add(program)
            elif program is None:
                no_program += 1
            elif bank.is_valid(program) and not bank.lookup(program, value.key, value.velocity):
                bad_keys[program].add(value.key)
        where = f"'{part.label}' (ch{part.channel + 1})"
        if no_program:
            errors.append(f"{where}: {no_program} notes before any program change")
        for program in sorted(bad_programs):
            errors.append(f"{where}: instrument {program} doesn't exist "
                          f"(valid: 1-{bank.max_program}, not empty slots)")
        for program, keys in sorted(bad_keys.items()):
            errors.append(f"{where}: instrument {program} has no sample for keys "
                          f"{sorted(keys)}")
    if errors:
        raise MidiConversionError("\n  ".join(["would crash the game:"] + errors))


# --------------------------------------------------------------------------
# Loop detection
# --------------------------------------------------------------------------

def _bar_ticks(song: Song) -> int:
    num, den = song.time_sig
    return max(1, song.division * 4 * num // den)


def _round_up(value: int, step: int) -> int:
    return -(-value // step) * step


def _marker_loop(song: Song) -> Tuple[Optional[int], Optional[int]]:
    start = end = None
    for tick, text in song.markers:
        key = text.lower().replace(" ", "").replace("_", "").replace("-", "")
        if key in ("loopstart", "loop", "[", "start") and start is None:
            start = tick
        elif key in ("loopend", "]", "end") and end is None:
            end = tick
    if start is None and song.cc111:
        start = song.cc111[0]
    return start, end


def detect_repeat(parts: List[Part], division: int, bar: int
                  ) -> Optional[Tuple[int, int]]:
    """Find a section that is played twice in a row (intro + loop + loop...).

    Returns (loop_start, loop_length) for the longest period P (whole bars)
    such that everything from loop_start to the last note repeats exactly P
    ticks later, over at least half a period (and 4 bars).
    """
    notes = sorted(((n.tick, n.channel, n.key) for p in parts for n in p.notes))
    if len(notes) < 32:
        return None
    present = set(notes)
    last = notes[-1][0]
    best = None
    for period in range(bar * 4, last + 1, bar):
        # Earliest start such that every note in [start, last - period] is
        # repeated period ticks later and every note in [start + period, last]
        # appeared period ticks earlier.
        bad = -1
        for tick, channel, key in reversed(notes):
            if tick + period > last:
                continue
            if (tick + period, channel, key) not in present:
                bad = tick
                break
        for tick, channel, key in reversed(notes):
            if tick < period:
                break
            if (tick - period, channel, key) not in present:
                bad = max(bad, tick - period)
                break
        start = _round_up(bad + 1, division)
        matched = last - period - start
        if matched < max(bar * 4, period // 2):
            continue
        count = sum(1 for t, _, _ in notes if start <= t <= last - period)
        if count < 16:
            continue
        best = (start, period)          # keep the longest period
    return best


def choose_loop(song: Song, parts: List[Part], loop_cfg
                ) -> Optional[Tuple[int, int]]:
    bar = _bar_ticks(song)
    last_end = max((n.tick + n.duration for p in parts for n in p.notes), default=0)
    last_on = max((n.tick for p in parts for n in p.notes), default=0)
    song_end = max(bar, _round_up(max(last_on + 1, last_end - song.division // 2), bar))

    if loop_cfg is False or (isinstance(loop_cfg, str) and loop_cfg.lower() in ("false", "off", "none")):
        return None
    if isinstance(loop_cfg, dict):
        start = int(loop_cfg.get("start", 0))
        end = int(loop_cfg.get("end", song_end))
        logger.info(f"Music:   loop from config: ticks {start} -> {end}")
        return start, end

    start, end = _marker_loop(song)
    if start is not None or end is not None:
        start = start or 0
        end = end if end is not None else song_end
        logger.info(f"Music:   loop from MIDI markers: ticks {start} -> {end}")
        return start, end

    repeat = detect_repeat(parts, song.division, bar)
    if repeat:
        start, period = repeat
        logger.info(f"Music:   detected repeated section: loop ticks {start} -> "
                    f"{start + period} (bar {start / bar + 1:g} -> {(start + period) / bar + 1:g})")
        return start, start + period

    logger.info(f"Music:   no repeat found, looping the whole song: ticks 0 -> "
                f"{song_end} ({song_end // bar} bars)")
    return 0, song_end


# --------------------------------------------------------------------------
# Compressed sequence writer
# --------------------------------------------------------------------------

def _varlen(value: int) -> bytes:
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(out))


class _Compressor:
    """Writes one track, replacing repeated byte runs with FE back-references."""

    MIN_MATCH = 5
    MAX_MATCH = 255
    MAX_OFFSET = 0xFDFF   # high byte must not be 0xFE
    MAX_CANDIDATES = 64

    def __init__(self, compress: bool = True):
        self.out = bytearray()
        self.referencable: List[bool] = []
        self.index: Dict[bytes, List[int]] = defaultdict(list)
        self.compress = compress

    def _emit(self, data: bytes, referencable: bool = True) -> None:
        for byte in data:
            pos = len(self.out)
            self.out.append(byte)
            # FE bytes are escape codes in the stream, never copyable as-is
            self.referencable.append(referencable and byte != 0xFE)
            if pos >= 2:
                key = bytes(self.out[pos - 2:pos + 1])
                if all(self.referencable[pos - 2:pos + 1]):
                    self.index[key].append(pos - 2)

    def write(self, raw: bytes) -> None:
        """Write a run of plain event bytes (may be compressed)."""
        i = 0
        while i < len(raw):
            best_len, best_src = 0, 0
            if self.compress and i + self.MIN_MATCH <= len(raw):
                here = len(self.out)
                for src in reversed(self.index.get(raw[i:i + 3], [])[-self.MAX_CANDIDATES:]):
                    if here - src > self.MAX_OFFSET:
                        break
                    length = 0
                    limit = min(self.MAX_MATCH, len(raw) - i, here - src)
                    while (length < limit and self.referencable[src + length]
                           and self.out[src + length] == raw[i + length]):
                        length += 1
                    if length > best_len:
                        best_len, best_src = length, src
                        if length == limit:
                            break
            if best_len >= self.MIN_MATCH:
                offset = len(self.out) - best_src
                self._emit(bytes((0xFE, offset >> 8, offset & 0xFF, best_len)),
                           referencable=False)
                i += best_len
            else:
                byte = raw[i]
                self._emit(b"\xFE\xFE" if byte == 0xFE else bytes((byte,)))
                i += 1

    def write_fixed(self, raw: bytes) -> None:
        """Write bytes read directly by the player (never escaped/compressed)."""
        self._emit(raw, referencable=False)


def _track_events(part: Part, tempos, loop, include_tempo: bool,
                  bend_rpn: bool):
    """Build the (tick, order, bytes, kind) list of one output track."""
    events = []
    channel = part.channel

    def add(tick, order, data, kind="event"):
        events.append((tick, order, data, kind))

    if bend_rpn:
        add(0, 1, bytes((0xB0 | channel, 101, 0)))
        add(0, 1, bytes((0xB0 | channel, 100, 0)))
        add(0, 1, bytes((0xB0 | channel, 6, 2)))
    if include_tempo:
        for tick, tempo in tempos:
            add(tick, 0, bytes((0xFF, 0x51)) + tempo.to_bytes(3, "big"))
    for tick, status, args in part.events:
        add(tick, 1, bytes((status,)) + args)
    for note in part.notes:
        add(note.tick, 2, (bytes((0x90 | channel, note.key, note.velocity))
                           + _varlen(note.duration)), "note")

    if loop:
        start, end = loop
        events = [e for e in events if e[0] < end]
        # State re-sent right after the loop start (only what changes in the loop)
        state_at_start, changes = {}, set()
        for tick, order, data, kind in sorted(events, key=lambda e: (e[0], e[1])):
            if kind != "event":
                continue
            key = (data[0], data[1]) if data[0] & 0xF0 == 0xB0 else data[0]
            if data[0] & 0xF0 == 0xB0 and data[1] not in SNAPSHOT_CCS:
                continue
            if data[0] & 0xF0 not in (0xB0, 0xC0, 0xE0) and data[0] != 0xFF:
                continue
            if tick <= start:
                state_at_start[key] = data
            elif tick < end:
                changes.add(key)
        for key in changes:
            if key in state_at_start:
                add(start, -1, state_at_start[key])
        add(start, -2, b"", "loopstart")
        add(end, 9, b"", "loopend")

    events.sort(key=lambda e: (e[0], e[1]))
    return events


def write_cseq(parts: List[Part], division: int, tempos, loop,
               compress: bool = True) -> bytes:
    if len(parts) > 16:
        raise ValueError("more than 16 channels")
    header = bytearray(0x44)
    struct.pack_into(">I", header, 0x40, division)
    body = bytearray()

    for track_num, part in enumerate(parts):
        bend_rpn = any((s & 0xF0) == 0xE0 for _, s, _ in part.events) and not any(
            (s & 0xF0) == 0xB0 and a[0] in (100, 101) for _, s, a in part.events)
        events = _track_events(part, tempos, loop, track_num == 0, bend_rpn)

        writer = _Compressor(compress)
        last_tick = 0
        running = 0
        loop_target = None
        for tick, _, data, kind in events:
            delta = _varlen(tick - last_tick)
            last_tick = tick
            if kind == "loopstart":
                writer.write(delta + b"\xFF\x2E\x00\xFF")
                loop_target = len(writer.out)
                running = 0          # first event of the loop needs its status
                continue
            if kind == "loopend":
                writer.write(delta + b"\xFF\x2D")
                end_of_event = len(writer.out) + 6
                writer.write_fixed(b"\xFF\xFF" + struct.pack(">I", end_of_event - loop_target))
                continue
            if data[0] == 0xFF:
                writer.write(delta + data)
                running = 0
                continue
            if data[0] == running:
                writer.write(delta + data[1:])
            else:
                writer.write(delta + data)
                running = data[0]
        writer.write(_varlen(0) + b"\xFF\x2F")

        struct.pack_into(">I", header, track_num * 4, 0x44 + len(body))
        body += writer.out
        while len(body) % 4:
            body.append(0)

    return bytes(header + body)


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------

def convert(midi_path: str, out_path: str, config: Optional[dict] = None,
            bank=None) -> None:
    """Convert; raises MidiConversionError if the result would crash the game.

    bank: smashremix_extra.audio.bank.Bank to validate against (skipped if None).
    """
    config = config or {}
    with open(midi_path, "rb") as f:
        song = parse_midi(f.read())
    logger.info(f"Music: converting {midi_path} ({len(song.tracks)} tracks, "
                f"division {song.division})")
    parts = assign_channels(song)
    if not parts:
        raise MidiConversionError("MIDI file has no notes")
    fixed = fix_overlaps(parts)
    if fixed:
        logger.info(f"Music:   fixed {fixed} doubled/overlapping notes")
    if bank is not None:
        validate(parts, bank)
    loop = choose_loop(song, parts, config.get("loop", "auto"))
    data = write_cseq(parts, song.division, song.tempos, loop)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(data)
    logger.info(f"Music: wrote {out_path} ({len(data)} bytes)")


def main(argv: List[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="MIDI -> N64 compressed sequence")
    parser.add_argument("midi")
    parser.add_argument("out")
    parser.add_argument("yaml", nargs="?")
    parser.add_argument("--rom", help="original.z64, to validate instruments")
    parser.add_argument("--midi-asm", help="Smash Remix src/midi.asm (with --rom)")
    args = parser.parse_args(argv[1:])
    config = {}
    if args.yaml:
        import yaml
        with open(args.yaml, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    bank = None
    if args.rom and args.midi_asm:
        from smashremix_extra.audio.bank import load_bank
        bank = load_bank(args.rom, args.midi_asm)
    try:
        convert(args.midi, args.out, config, bank)
    except MidiConversionError as e:
        print(f"{args.midi}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
