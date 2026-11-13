"""Material animations (MObj material scripts: texture flips, scrolling, palette and color
changes) <-> keyframes.

A stage layer's / item's p_matanim_joints gives each MObj an AObjEvent32 script (decomp
objanim.c gcParseMObjMatAnimJoint / gcPlayMObjMatAnim). Its tracks:

  scalar (flag bits 0..9 of SetVal* events, ops 3..11; track nGCAnimTrackMaterialStart + i)
    tex       texture_id_curr: the MObj image list entry drawn (flag 0x01)
    trau/trav tile 0 offset, in tile widths / heights (MObj flag 0x20: gDPSetTileSize)
    scau/scav tile 0 scale (divides the offset; with flag 0x80 also gSPTexture)
    tex_next  texture_id_next (flags 0x02 / 0x10: a second image)
    scrollu/v tile 1 offset (flag 0x40: the second tile, two-texture combiners)
    lfrac     prim LOD fraction (flag 0x08; 0x10: picks / blends two list images)
    pal       palette_id: the palette list entry (flag 0x04)
  color (flag bits 0..4 of SetExtVal* events, ops 18..21; RGBA words)
    prim, env, blend, light1, light2 (flags 0x200, 0x400, 0x800, 0x1000, 0x2000)

The game plays a script one frame at a time (60 per second). `simulate` plays it exactly
like the game and gives each animated track's value per frame; `to_keys` turns those into
keyframes (CONSTANT: the value holds, then steps; LINEAR) that reproduce every frame
exactly; `encode` writes keyframes (CONSTANT / LINEAR / BEZIER = cubic with slopes) back
as a script, checked by simulating it.
"""
import struct

TRACKS = ('tex', 'trau', 'trav', 'scau', 'scav', 'tex_next', 'scrollu', 'scrollv', 'lfrac', 'pal')
COLORS = ('prim', 'env', 'blend', 'light1', 'light2')
DISCRETE = {'tex', 'tex_next', 'pal'}               # indices: the game truncates them
# Blender material custom properties (animatable) per track
PROP = {'tex': 'n64_tex_index', 'trau': 'n64_offset_x', 'trav': 'n64_offset_y', 'scau': 'n64_scale_x',
        'scav': 'n64_scale_y', 'tex_next': 'n64_tex_next', 'scrollu': 'n64_scroll2_x', 'scrollv': 'n64_scroll2_y',
        'lfrac': 'n64_lod_frac', 'pal': 'n64_pal_index',
        'prim': 'n64_prim_color', 'env': 'n64_env_color', 'blend': 'n64_blend_color',
        'light1': 'n64_light1_color', 'light2': 'n64_light2_color'}
TRACK_OF_PROP = {v: k for k, v in PROP.items()}
# the MObj flags a track needs to have an effect
FLAG_OF = {'tex': 0x01, 'trau': 0x20, 'trav': 0x20, 'scau': 0x20, 'scav': 0x20, 'tex_next': 0x02,
           'scrollu': 0x40, 'scrollv': 0x40, 'lfrac': 0x08, 'pal': 0x04,
           'prim': 0x200, 'env': 0x400, 'blend': 0x800, 'light1': 0x1000, 'light2': 0x2000}
# MObjSub fields (static values: what a track starts from)
FIELD = {'trau': 0x14, 'trav': 0x18, 'scau': 0x1C, 'scav': 0x20, 'scrollu': 0x3C, 'scrollv': 0x40}
COLOR_FIELD = {'prim': 0x50, 'env': 0x58, 'blend': 0x5C, 'light1': 0x60, 'light2': 0x64}

END, NULL, CHANGED = 'end', 'null', 'changed'
OP_END, OP_JUMP, OP_WAIT, OP_SETVAL, OP_SETVAL_AFTER, OP_RATE = 0, 1, 2, 4, 11, 7
OP_SETVAL_RATE, OP_EXT_AFTER, OP_EXT = 6, 19, 21


def u32(d, o):
    return struct.unpack_from('>I', d, o)[0]


def f32(d, o):
    return struct.unpack_from('>f', d, o)[0]


def f32r(v):
    """v as the game stores it (float32)."""
    return struct.unpack('>f', struct.pack('>f', v))[0]


def mobj_static(d, ms):
    """What an MObj starts with (gcAddMObjForDObj): its MObjSub's values, image / palette 0."""
    out = {k: f32(d, ms + o) for k, o in FIELD.items()}
    out.update(tex=0.0, tex_next=0.0, pal=0.0, lfrac=d[ms + 0x54] / 255.0)
    for k, o in COLOR_FIELD.items():
        out[k] = u32(d, ms + o)
    return out


def tile_info(d, ms):
    """The MObjSub's tile fields the offset tracks are drawn with (objdisplay.c
    gcDrawMObjForDObj): flags, mode (unk10), unk0A, tile 0 size (unk0C/0E), tile 1 size
    (unk38/3A), unk08 (gSPTexture), and the starting offset / scale."""
    s, t = struct.unpack_from('>HHHH', d, ms + 8), struct.unpack_from('>HH', d, ms + 0x38)
    return dict(flags=struct.unpack_from('>H', d, ms + 0x30)[0], mode=struct.unpack_from('>i', d, ms + 0x10)[0],
                unk08=s[0], unk0a=s[1], size0=[s[2], s[3]], size1=list(t))


# ---------------------------------------------------------------- playing a script
class _AObj:
    __slots__ = ('kind', 'vb', 'vt', 'rb', 'rt', 'len', 'li', 'color')

    def __init__(self, color):
        self.kind, self.vb, self.vt, self.rb, self.rt, self.len, self.li = None, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0
        self.color = color
        if color:
            self.vb = self.vt = 0


def _value(a):
    if a.kind == 'linear':
        return f32r(a.vb + a.len * a.rb)
    if a.kind == 'cubic':
        li, ln = a.li, a.len
        f16, f12 = li * li, ln * ln
        f18 = li * f12
        f14 = ln * f12 * f16
        f20 = 2.0 * f14 * li
        f22 = 3.0 * f12 * f16
        f24 = f14 - f18
        return f32r(a.vb * ((f20 - f22) + 1.0) + a.vt * (f22 - f20) + a.rb * ((f24 - f18) + ln) + a.rt * f24)
    if a.kind == 'step':
        return a.vt if a.li <= a.len else a.vb
    return None


def _color(a):
    if a.kind == 'linear':
        i = max(0, min(256, int(a.len * a.li * 256.0)))
        return sum(((((a.vb >> sh) & 255) * (256 - i) + ((a.vt >> sh) & 255) * i) >> 8) << sh
                   for sh in (24, 16, 8, 0))
    if a.kind == 'step':
        return a.vt if a.li <= a.len else a.vb
    return None


def simulate(d, ptrs, script, frames, stop_at_loop=True):
    """Play a material script like the game -> (per frame {track: value} of the tracks it
    has set so far, loop) where loop = (start, end): at frame `end` the script jumps back to
    where it was at frame `start` (a cycle of end - start frames), or ('end', frame) when
    it ends, or None. Colors are RGBA words. With stop_at_loop, stops one cycle after the
    loop is found (enough to check it)."""
    aobjs = {}
    wait, frame, ptr = CHANGED, 0.0, script
    entered, loop, out, last = {script: 0}, None, [], {}
    stop = frames
    for n in range(frames):
        if n >= stop:
            break
        if wait not in (NULL, END):
            parse = True
            if wait == CHANGED:
                wait = -frame
            else:
                wait -= 1.0
                frame += 1.0
                parse = wait <= 0.0
            while parse:
                if ptr is None or ptr + 4 > len(d):
                    for a in aobjs.values():
                        if a.kind:
                            a.len += 1.0 + wait
                    frame, wait = wait, END
                    loop = loop or ('end', n)
                    break
                w = u32(d, ptr)
                op, flags, payload = w >> 25, (w >> 15) & 0x3FF, float(w & 0x7FFF)
                if op in (8, 9, 4, 3, 6, 5, 10, 11, 7, 12):
                    ptr += 4
                    for i in range(10):
                        if not flags >> i:
                            break
                        if not (flags >> i) & 1:
                            continue
                        a = aobjs.setdefault(TRACKS[i], _AObj(False))
                        if op == 7:
                            a.rt = f32(d, ptr)
                            ptr += 4
                            continue
                        if op == 12:
                            a.len += payload
                            continue
                        a.vb, a.vt = a.vt, f32(d, ptr)
                        ptr += 4
                        if op in (8, 9):
                            a.rb, a.rt, a.kind = a.rt, 0.0, 'cubic'
                            if payload:
                                a.li = f32r(1.0 / payload)
                        elif op in (3, 4):
                            a.kind = 'linear'
                            if payload:
                                a.rb = f32r((a.vt - a.vb) / payload)
                            a.rt = 0.0
                        elif op in (5, 6):
                            a.rb, a.rt = a.rt, f32(d, ptr)
                            ptr += 4
                            a.kind = 'cubic'
                            if payload:
                                a.li = f32r(1.0 / payload)
                        else:                                      # 10, 11: value after
                            a.kind, a.li, a.rt = 'step', payload, 0.0
                        a.len = -wait - 1.0
                    if op in (8, 3, 5, 10):                        # the *Block variants wait
                        wait += payload
                elif op == OP_WAIT:
                    wait += payload
                    ptr += 4
                elif op in (OP_JUMP, 14):                          # jump / set anim
                    target = ptrs.get(ptr + 4)
                    if op == 14:
                        frame = -wait
                    if target in entered and loop is None:
                        loop = (entered[target], n)
                        if stop_at_loop:
                            stop = min(frames, n + (n - entered[target]) + 1)
                    entered.setdefault(target, n)
                    ptr = target
                elif op == OP_END:
                    for a in aobjs.values():
                        if a.kind:
                            a.len += 1.0 + wait
                    frame, wait = wait, END
                    loop = loop or ('end', n)
                    break
                elif op in (18, 19, 20, 21):
                    ptr += 4
                    for i in range(5):
                        if not flags >> i:
                            break
                        if not (flags >> i) & 1:
                            continue
                        a = aobjs.setdefault(COLORS[i], _AObj(True))
                        a.vb, a.vt = a.vt, u32(d, ptr)
                        ptr += 4
                        if op in (18, 19):
                            a.kind, a.li = 'step', payload
                        else:
                            a.kind = 'linear'
                            if payload:
                                a.li = f32r(1.0 / payload)
                        a.len = -wait - 1.0
                    if op in (18, 20):
                        wait += payload
                else:                                              # not a material command: stop
                    frame, wait = wait, END
                    loop = loop or ('end', n)
                    break
                if wait != END and wait > 0.0:
                    break
        if wait not in (NULL,):
            for name, a in aobjs.items():
                if a.kind:
                    if wait != END:
                        a.len += 1.0
                    last[name] = _color(a) if a.color else _value(a)
            if wait == END:
                wait = NULL
        out.append(dict(last))
    return out, loop


def play(d, ptrs, script, static, max_frames=1800, max_loop=16384):
    """A script's animation -> dict(frames, loop, loop_start, samples={track: [value per
    frame 0..frames]}) where, when it loops, frame `frames` is the cycle's end (= its
    start again: frame loop_start); otherwise the values hold after `frames` (at most
    max_frames: a script that neither ends nor loops). Cycles up to max_loop frames are
    found (e.g. a slow scroll: 4096 frames)."""
    out, loop = simulate(d, ptrs, script, 2 * max_loop + 2)         # (a cycle found: one more, to check)
    tracks = sorted({k for fr in out for k in fr}, key=lambda k: (TRACKS + COLORS).index(k))
    full = {}                                  # every frame played: a track holds its value until set
    for k in tracks:
        cur, seq = static.get(k, 0), []
        for fr in out:
            cur = fr.get(k, cur)
            seq.append(cur)
        full[k] = seq
    if loop and loop[0] != 'end':
        start, end = loop
        period = end - start
        ok = len(out) >= end + period and all(seq[start + x] == seq[end + x] for seq in full.values()
                                              for x in range(period))
        if ok:
            n, looping = end, True
        else:
            n, looping, start = min(len(out) - 1, max_frames), False, 0
    elif loop:
        n, looping, start = loop[1], False, 0
    else:
        n, looping, start = min(len(out) - 1, max_frames), False, 0
    samples = {k: seq[:n + 1] for k, seq in full.items()}
    return dict(frames=n, loop=looping, loop_start=start if looping else 0, samples=samples)


# ---------------------------------------------------------------- keyframes
def _close(a, b, track, color_tol=1, tol=None):
    if tol is not None:                         # (a caller's own tolerance: tol(value) -> max difference)
        return abs(a - b) <= tol(b)
    if track in COLORS:
        return all(abs(((a >> sh) & 255) - ((b >> sh) & 255)) <= color_tol for sh in (24, 16, 8, 0))
    return abs(a - b) <= 1e-4 * max(1.0, abs(a), abs(b))


def _linear_fits(seq, k, m, track, tol=None):
    if track in COLORS:
        D = m - k
        for x in range(1, D):
            i = max(0, min(256, int(x / D * 256.0)))
            want = sum(((((seq[k] >> sh) & 255) * (256 - i) + ((seq[m] >> sh) & 255) * i) >> 8) << sh
                       for sh in (24, 16, 8, 0))
            if not _close(seq[k + x], want, track):
                return False
        return True
    for x in range(k + 1, m):
        if not _close(seq[x], seq[k] + (seq[m] - seq[k]) * (x - k) / (m - k), track, tol=tol):
            return False
    return True


def to_keys(seq, track, tol=None):
    """Per-frame values (frames 0..n) -> [[frame, value, 'CONSTANT' | 'LINEAR'], ...] that
    give the same value at every frame: a held value is one CONSTANT key (stepping at the
    next key), a steady change one LINEAR segment. Indices are always CONSTANT."""
    n = len(seq) - 1
    keys, k = [], 0
    while k < n:
        m = k + 1
        while m <= n and (seq[m] == seq[k] if track in COLORS else _close(seq[m], seq[k], track, tol=tol)):
            m += 1
        if m > k + 1 or track in DISCRETE or m > n:          # held (then it steps at m)
            keys.append([k, seq[k], 'CONSTANT'])
            k = min(m, n)
            continue
        while m + 1 <= n and _linear_fits(seq, k, m + 1, track, tol=tol):
            m += 1
        keys.append([k, seq[k], 'LINEAR' if m > k + 1 else 'CONSTANT'])
        k = m
    keys.append([n, seq[n], 'CONSTANT'])
    return keys


def eval_keys(keys, frame, track):
    """A to_keys key list's value at an integer frame (the same as Blender's)."""
    if frame <= keys[0][0]:
        return keys[0][1]
    for (t0, v0, ip), (t1, v1, _) in zip(keys, keys[1:]):
        if t0 <= frame < t1:
            if ip == 'CONSTANT':
                return v0
            u = (frame - t0) / (t1 - t0)
            if track in COLORS:
                return sum(int(round(((v0 >> sh) & 255) * (1 - u) + ((v1 >> sh) & 255) * u)) << sh
                           for sh in (24, 16, 8, 0))
            return v0 + (v1 - v0) * u
    return keys[-1][1]


# ---------------------------------------------------------------- writing a script
def _cmd(op, flags, payload):
    if not 0 <= payload <= 0x7FFF:
        raise ValueError(f'material animation: a segment of {payload} frames (max 32767)')
    return struct.pack('>I', (op << 25) | (flags << 15) | int(payload))


def encode(tracks, frames, loop, loop_start=0, names=TRACKS):
    """Keyframes -> a material script: (bytes, [(pointer field offset, target offset)]).
    tracks: {track: [(frame, value, interp, slope_in, slope_out), ...]} sorted by frame
    (interp: 'CONSTANT' / 'LINEAR' / 'BEZIER', slopes per frame for BEZIER; color values are
    RGBA words, CONSTANT / LINEAR only). frames: the end (loop: the frame it starts over,
    = loop_start's values again). Each track is set to its first key's value at frame 0
    (and at loop_start), then each segment is one event at its start."""
    events = {}                    # frame -> [(order, op, payload, track, values)]

    def ev(t, order, op, payload, track, vals):
        events.setdefault(t, []).append((order, op, int(payload), track, vals))

    def value_at(keys, t):
        if t <= keys[0][0]:
            return keys[0][1]
        for a, b in zip(keys, keys[1:]):
            if a[0] <= t < b[0]:
                if a[2] == 'CONSTANT':
                    return a[1]
                u = (t - a[0]) / (b[0] - a[0])
                if isinstance(a[1], int):                  # an RGBA word: per channel
                    i = max(0, min(256, int(u * 256.0)))
                    return sum(((((a[1] >> sh) & 255) * (256 - i) + ((b[1] >> sh) & 255) * i) >> 8) << sh
                               for sh in (24, 16, 8, 0))
                if a[2] == 'LINEAR':
                    return a[1] + (b[1] - a[1]) * u
                D = b[0] - a[0]
                return (a[1] * (2 * u ** 3 - 3 * u ** 2 + 1) + b[1] * (3 * u ** 2 - 2 * u ** 3) +
                        a[4] * D * (u ** 3 - 2 * u ** 2 + u) + b[3] * D * (u ** 3 - u ** 2))
        return keys[-1][1]
    for track, keys in tracks.items():
        color = track in COLORS
        keys = [k for k in keys if 0 <= k[0] <= frames] or [keys[0]]
        starts = [0] + ([loop_start] if loop and loop_start else [])
        for s in starts:            # the value it starts from (a jump back: its value again)
            v = value_at(keys, s)
            ev(s, 0, OP_EXT_AFTER if color else OP_SETVAL_AFTER, 0, track,
               [int(v) & 0xFFFFFFFF] if color else [float(v)])
        for a, b in zip(keys, keys[1:]):
            D = b[0] - a[0]
            if D <= 0:
                continue
            ip = a[2]
            if color:
                op = OP_EXT if ip != 'CONSTANT' else OP_EXT_AFTER
                ev(a[0], 1, op, D, track, [int(b[1]) & 0xFFFFFFFF])
            elif ip == 'CONSTANT':
                ev(a[0], 1, OP_SETVAL_AFTER, D, track, [float(b[1])])
            elif ip == 'LINEAR':
                ev(a[0], 1, OP_SETVAL, D, track, [float(b[1])])
            else:                    # cubic: the start slope (target rate), then the end value + slope
                ev(a[0], 1, OP_RATE, 0, track, [float(a[4])])
                ev(a[0], 2, OP_SETVAL_RATE, D, track, [float(b[1]), float(b[3])])
        last = keys[-1]
        if last[0] < frames and len(keys) > 1:       # hold the last value (a line would go on)
            ev(last[0], 3, OP_EXT_AFTER if color else OP_SETVAL_AFTER, 0, track,
               [int(last[1]) & 0xFFFFFFFF] if color else [float(last[1])])
    out, ptrs, at, t = b'', [], {}, 0
    for frame in sorted(events):
        if frame > frames:
            break
        if frame > t:
            out += _cmd(OP_WAIT, 0, frame - t)
            t = frame
        at.setdefault(frame, len(out))
        groups = {}                  # one command per (order, op, payload): flags in track order
        for order, op, payload, track, vals in events[frame]:
            groups.setdefault((order, op, payload), []).append((track, vals))
        for (order, op, payload), items in sorted(groups.items()):
            names_ = COLORS if op in (18, 19, 20, 21) else names
            items.sort(key=lambda x: names_.index(x[0]))
            flags = sum(1 << names_.index(tr) for tr, _ in items)
            out += _cmd(op, flags, payload)
            for tr, vals in items:
                for v in vals:
                    out += struct.pack('>I', v) if isinstance(v, int) else struct.pack('>f', v)
    if frames > t:
        out += _cmd(OP_WAIT, 0, frames - t)
    if loop:
        target = at.get(loop_start, 0)
        out += _cmd(OP_JUMP, 0, 0)
        ptrs.append((len(out), target))
        out += b'\0\0\0\0'
    else:
        out += _cmd(OP_END, 0, 0)
    return out, ptrs


def check(script, ptrs, samples, frames, loop, loop_start=0):
    """Tracks whose values, playing `script`, differ from `samples` ({track: values for
    frames 0..frames}; looping: frame `frames` is loop_start's again)."""
    d = script + b'\0' * 8
    period = frames - loop_start
    total = frames + 1 + (period if loop else 0)
    out, _ = simulate(d, dict(ptrs), 0, total, stop_at_loop=False)
    bad = []
    for k, seq in samples.items():
        for n in range(len(out)):
            if loop and period > 0 and n >= frames:
                want = seq[loop_start + (n - frames) % period]
            else:
                want = seq[min(n, len(seq) - 1)]
            have = out[n].get(k)
            # (colors: the game's fade truncates, Blender's rounds a straight line: 2 apart at most)
            if have is None or not _close(have, want, k, color_tol=2):
                bad.append(k)
                break
    return bad


def baked(seq, track):
    """Keys for every frame (exact, when a curve can't be written as segments)."""
    ip = 'CONSTANT' if track in DISCRETE else 'LINEAR'
    return [(n, v, ip, 0.0, 0.0) for n, v in enumerate(seq)]


def build(tracks, samples, frames, loop, loop_start=0):
    """encode() checked against the per-frame values the keys should give (Blender's curves,
    `samples`): a track that doesn't come out right is written frame by frame. ->
    (bytes, pointer fields, [tracks baked])."""
    script, ptrs = encode(tracks, frames, loop, loop_start)
    bad = check(script, ptrs, samples, frames, loop, loop_start)
    if bad:
        tracks = dict(tracks)
        for k in bad:
            tracks[k] = baked(samples[k], k)
        script, ptrs = encode(tracks, frames, loop, loop_start)
    return script, ptrs, bad
