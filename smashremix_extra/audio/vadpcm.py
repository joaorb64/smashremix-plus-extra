"""Pure-Python Nintendo VADPCM (AIFF-C ``VAPC``) encoder.

Port of the N64 SDK ``tabledesign`` + ``vadpcm_enc`` tools (as preserved in the
n64decomp sdk-tools) so the appender can accept ``sounds/<name>.wav`` and emit
the ``<name>.aifc`` the bass build expects - with no external binaries or
libaudiofile/ffmpeg dependency. Only Python's stdlib ``wave`` is used to read
the source audio.

The emitted AIFF-C has a byte-for-byte fixed chunk layout (FORM/COMM/INST/
APPL VADPCMCODES/SSND) so ``src/FGM.asm``'s hard-coded offsets keep working:
predictors at 0x70 (0x80 bytes), SSND size u32 at 0xF4, sample data at 0x100.
That requires ``order == 2`` and ``npredictors == 4`` (the tool defaults), which
``encode()`` asserts.

Public API:
    read_wav(path) -> (samples: list[int], sample_rate: int)
    tabledesign(samples, order=2, bits=2, ...) -> (order, npredictors, table)
    encode(samples, sample_rate, table=None) -> bytes         # full .aifc
    wav_to_aifc(wav_path, aifc_path, sample_rate=None) -> int  # sample_rate used
    decode_aifc(data) -> (samples, sample_rate)                # for tests
"""

from __future__ import annotations

import struct
import wave

__all__ = [
    "read_wav", "write_wav", "tabledesign", "encode", "wav_to_aifc",
    "decode_aifc", "aifc_to_wav", "convert_dir", "AudioError",
]


def convert_dir(sounds_dir, rate_for=None):
    """Compile every ``<name>.wav`` in `sounds_dir` to ``<name>.aifc`` (unless a
    newer .aifc already exists). `rate_for(name)` optionally returns the target
    sample rate for a sound (default: the WAV's own rate). Returns the list of
    (name, rate) converted. Safe to call when the dir has no WAVs / doesn't
    exist. Never raises - logs and skips on a per-file error."""
    import os
    from smashremix_extra.logger import logger

    done = []
    if not sounds_dir or not os.path.isdir(sounds_dir):
        return done
    for fn in sorted(os.listdir(sounds_dir)):
        if not fn.lower().endswith(".wav"):
            continue
        name = fn[:-4]
        wav = os.path.join(sounds_dir, fn)
        aifc = os.path.join(sounds_dir, name + ".aifc")
        if (os.path.exists(aifc)
                and os.path.getmtime(aifc) >= os.path.getmtime(wav)):
            continue
        rate = None
        if rate_for is not None:
            try:
                rate = rate_for(name)
            except Exception:                     # noqa: BLE001
                rate = None
        try:
            used = wav_to_aifc(wav, aifc, rate)
            logger.info("sound: compiled %s -> %s.aifc (%d Hz)",
                        fn, name, used)
            done.append((name, used))
        except Exception as e:                     # noqa: BLE001
            logger.error("sound: failed to compile %s (%s)", wav, e)
    return done


class AudioError(Exception):
    pass


# --------------------------------------------------------------------------- #
# WAV input (stdlib only)                                                     #
# --------------------------------------------------------------------------- #
def read_wav(path):
    """Read a 16-bit PCM mono WAV. Returns (samples[int], sample_rate)."""
    with wave.open(str(path), "rb") as w:
        ch, width, rate, nframes = (
            w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes())
        raw = w.readframes(nframes)
    if width != 2:
        raise AudioError(
            f"{path}: {width * 8}-bit samples; only 16-bit PCM WAV supported")
    samples = list(struct.unpack(f"<{len(raw) // 2}h", raw))
    if ch == 2:                                   # downmix to mono
        samples = [(samples[i] + samples[i + 1]) // 2
                   for i in range(0, len(samples), 2)]
    elif ch != 1:
        raise AudioError(f"{path}: {ch} channels; only mono/stereo supported")
    return samples, rate


def write_wav(path, samples, sample_rate):
    """Write mono s16 `samples` to a 16-bit PCM WAV."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(struct.pack(
            f"<{len(samples)}h",
            *(max(-32768, min(32767, int(s))) for s in samples)))


def aifc_to_wav(aifc_path, wav_path):
    """Decode a VADPCM AIFF-C to a 16-bit PCM WAV. Returns the sample rate."""
    samples, rate = decode_aifc(open(aifc_path, "rb").read())
    write_wav(wav_path, samples, rate)
    return rate


# --------------------------------------------------------------------------- #
# tabledesign - predictor codebook estimation                                 #
# --------------------------------------------------------------------------- #
def _acvect(hist, n, m):
    # out[i] = -sum_j in[j-i]*in[j]  (in indexed with `order` samples of history)
    out = [0.0] * (n + 1)
    for i in range(n + 1):
        s = 0.0
        for j in range(m):
            s -= hist[j - i + n] * hist[j + n]
        out[i] = s
    return out


def _acmat(hist, n, m):
    out = [[0.0] * (n + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, n + 1):
            s = 0.0
            for k in range(m):
                s += hist[k - i + n] * hist[k - j + n]
            out[i][j] = s
    return out


def _lud(a, n):
    """LU-decompose a (1-indexed (n+1)x(n+1)); returns (indx, ok)."""
    indx = [0] * (n + 1)
    vv = [0.0] * (n + 1)
    for i in range(1, n + 1):
        big = 0.0
        for j in range(1, n + 1):
            big = max(big, abs(a[i][j]))
        if big == 0.0:
            return indx, False
        vv[i] = 1.0 / big
    imax = 0
    for j in range(1, n + 1):
        for i in range(1, j):
            s = a[i][j]
            for k in range(1, i):
                s -= a[i][k] * a[k][j]
            a[i][j] = s
        big = 0.0
        for i in range(j, n + 1):
            s = a[i][j]
            for k in range(1, j):
                s -= a[i][k] * a[k][j]
            a[i][j] = s
            dum = vv[i] * abs(s)
            if dum >= big:
                big = dum
                imax = i
        if j != imax:
            for k in range(1, n + 1):
                a[imax][k], a[j][k] = a[j][k], a[imax][k]
            vv[imax] = vv[j]
        indx[j] = imax
        if a[j][j] == 0.0:
            return indx, False
        if j != n:
            dum = 1.0 / a[j][j]
            for i in range(j + 1, n + 1):
                a[i][j] *= dum
    mn, mx = 1e10, 0.0
    for i in range(1, n + 1):
        t = abs(a[i][i])
        mn = min(mn, t)
        mx = max(mx, t)
    return indx, (mn / mx >= 1e-10)


def _lubksb(a, n, indx, b):
    ii = 0
    for i in range(1, n + 1):
        ip = indx[i]
        s = b[ip]
        b[ip] = b[i]
        if ii:
            for j in range(ii, i):
                s -= a[i][j] * b[j]
        elif s:
            ii = i
        b[i] = s
    for i in range(n, 0, -1):
        s = b[i]
        for j in range(i + 1, n + 1):
            s -= a[i][j] * b[j]
        b[i] = s / a[i][i]


def _durbin(arg0, n):
    """Returns (k[0..n], a[0..n], div)."""
    a = [0.0] * (n + 1)
    k = [0.0] * (n + 1)
    a[0] = 1.0
    div = arg0[0]
    for i in range(1, n + 1):
        s = 0.0
        for j in range(1, i):
            s += a[j] * arg0[i - j]
        ki = -(arg0[i] + s) / div if div > 0.0 else 0.0
        a[i] = ki
        k[i] = ki
        for j in range(1, i):
            a[j] += a[i - j] * ki
        div *= 1.0 - ki * ki
    return k, a, div


def _afromk(kv, n):
    out = [0.0] * (n + 1)
    out[0] = 1.0
    for i in range(1, n + 1):
        out[i] = kv[i]
        for j in range(1, i):
            out[j] += out[i - j] * out[i]
    return out


def _kfroma(in_, n):
    """In-place-ish; returns (k[0..n], overflow_count) or (None, 1) on failure."""
    in_ = list(in_)
    out = [0.0] * (n + 1)
    ret = 0
    out[n] = in_[n]
    for i in range(n - 1, 0, -1):
        nxt = [0.0] * (i + 1)
        bad = False
        for j in range(i + 1):
            temp = out[i + 1]
            div = 1.0 - temp * temp
            if div == 0.0:
                bad = True
                break
            nxt[j] = (in_[j] - in_[i + 1 - j] * temp) / div
        if bad:
            return None, 1
        for j in range(i + 1):
            in_[j] = nxt[j]
        out[i] = nxt[i]
        if abs(out[i]) > 1.0:
            ret += 1
    return out, ret


def _rfroma(arg0, n):
    mat = [None] * (n + 1)
    mat[n] = [0.0] * (n + 1)
    mat[n][0] = 1.0
    for i in range(1, n + 1):
        mat[n][i] = -arg0[i]
    for i in range(n, 0, -1):
        mat[i - 1] = [0.0] * i
        div = 1.0 - mat[i][i] * mat[i][i]
        for j in range(1, i):
            mat[i - 1][j] = (mat[i][i - j] * mat[i][i] + mat[i][j]) / div
    out = [0.0] * (n + 1)
    out[0] = 1.0
    for i in range(1, n + 1):
        out[i] = 0.0
        for j in range(1, i + 1):
            out[i] += mat[i][j] * out[i - j]
    return out


def _model_dist(a, b, n):
    sp3c = _rfroma(b, n)
    sp38 = [0.0] * (n + 1)
    for i in range(n + 1):
        for j in range(n - i + 1):
            sp38[i] += a[j] * a[i + j]
    ret = sp38[0] * sp3c[0]
    for i in range(1, n + 1):
        ret += 2 * sp3c[i] * sp38[i]
    return ret


def _split(table, delta, order, npredictors, scale):
    for i in range(npredictors):
        for j in range(order + 1):
            table[i + npredictors][j] = table[i][j] + delta[j] * scale


def _refine(table, order, npredictors, data, refine_iters):
    for _ in range(refine_iters):
        counts = [0] * npredictors
        rsums = [[0.0] * (order + 1) for _ in range(npredictors)]
        for row in data:
            best_v, best_i = 1e30, 0
            for j in range(npredictors):
                d = _model_dist(table[j], row, order)
                if d < best_v:
                    best_v, best_i = d, j
            counts[best_i] += 1
            t = _rfroma(row, order)
            for j in range(order + 1):
                rsums[best_i][j] += t[j]
        for i in range(npredictors):
            if counts[i] > 0:
                for j in range(order + 1):
                    rsums[i][j] /= counts[i]
        for i in range(npredictors):
            _, a, _div = _durbin(rsums[i], order)
            for j in range(1, order + 1):
                a[j] = min(0.9999999999, max(-0.9999999999, a[j]))
            table[i] = _afromk(a, order)


def tabledesign(samples, order=2, bits=2, refine_iters=2, frame_size=16,
                thresh=10.0):
    """Estimate a VADPCM predictor codebook. Returns (order, npredictors,
    table) where table is npredictors lists of `order` rows of 8 ints."""
    data = []
    hist = [0] * (frame_size * 2)          # [ ...history... | frame ]
    pos = 0
    n = len(samples)
    while pos + frame_size <= n:
        frame = samples[pos:pos + frame_size]
        for i in range(frame_size):
            hist[frame_size + i] = frame[i]
        # window used by acvect/acmat is hist[frame_size + (k-i)] with the
        # `order` samples before the frame as history.
        win = hist[frame_size - order: frame_size * 2]

        def h(idx):                        # idx in [-order, frame_size)
            return win[idx + order]

        vec = [0.0] * (order + 1)
        for i in range(order + 1):
            s = 0.0
            for j in range(frame_size):
                s -= h(j - i) * h(j)
            vec[i] = s
        if abs(vec[0]) > thresh:
            mat = [[0.0] * (order + 1) for _ in range(order + 1)]
            for i in range(1, order + 1):
                for j in range(1, order + 1):
                    s = 0.0
                    for k in range(frame_size):
                        s += h(k - i) * h(k - j)
                    mat[i][j] = s
            indx, ok = _lud(mat, order)
            if ok:
                _lubksb(mat, order, indx, vec)
                vec[0] = 1.0
                kv, bad = _kfroma(vec, order)
                if kv is not None:
                    for i in range(1, order + 1):
                        kv[i] = min(0.9999999999, max(-0.9999999999, kv[i]))
                    data.append(_afromk(kv, order))
        for i in range(frame_size):
            hist[i] = hist[i + frame_size]
        pos += frame_size

    if not data:
        raise AudioError("tabledesign: no usable frames (audio too quiet/short)")

    vec = [1.0] + [0.0] * order
    for row in data:
        t = _rfroma(row, order)
        for j in range(1, order + 1):
            vec[j] += t[j]
    for j in range(1, order + 1):
        vec[j] /= len(data)

    _, a, _div = _durbin(vec, order)
    for j in range(1, order + 1):
        a[j] = min(0.9999999999, max(-0.9999999999, a[j]))

    npred = 1 << bits
    table = [[0.0] * (order + 1) for _ in range(npred)]
    table[0] = _afromk(a, order)
    cur = 0
    while cur < bits:
        delta = [0.0] * (order + 1)
        delta[order - 1] = -1.0
        _split(table, delta, order, 1 << cur, 0.01)
        cur += 1
        _refine(table, order, 1 << cur, data, refine_iters)

    npredictors = 1 << cur
    out = []
    for p in range(npredictors):
        out.append(_print_entry(table[p], order))
    return order, npredictors, out


def _print_entry(row, order):
    """Mirror tabledesign/print.c: build the 8x order predictor and quantise
    by *2048. Returns `order` rows of 8 ints."""
    table = [[0.0] * order for _ in range(8)]
    for i in range(order):
        for j in range(i):
            table[i][j] = 0.0
        for j in range(i, order):
            table[i][j] = -row[order - j + i]
    for i in range(order, 8):
        for j in range(order):
            table[i][j] = 0.0
    for i in range(1, 8):
        for j in range(1, order + 1):
            if i - j >= 0:
                for k in range(order):
                    table[i][k] -= row[j] * table[i - j][k]
    rows = []
    for i in range(order):
        line = []
        for j in range(8):
            fval = table[j][i] * 2048.0
            ival = int(fval - 0.5) if fval < 0.0 else int(fval + 0.5)
            line.append(ival)
        rows.append(line)
    return rows


# --------------------------------------------------------------------------- #
# VADPCM frame encoding (vadpcm_enc / vencode.c)                              #
# --------------------------------------------------------------------------- #
def _inner_product(length, v1, v2):
    out = 0
    for j in range(length):
        out += v1[j] * v2[j]
    # C: dout = out/2048 (trunc toward zero); adjust down when remainder < 0.
    # That is exactly Python floor division.
    return out >> 11 if out >= 0 else -((-out) >> 11) - (1 if out % 2048 else 0)


def _build_coef_table(order, npredictors, table):
    """Expand raw `table` (per-pred `order` rows of 8) into coefTable[p][k][col]
    with col in [0, order+8), mirroring vpredictor.c readcodebook()."""
    coef = []
    for p in range(npredictors):
        entry = [[0] * (order + 8) for _ in range(8)]
        for j in range(order):
            for k in range(8):
                entry[k][j] = table[p][j][k]
        for k in range(1, 8):
            entry[k][order] = entry[k - 1][order - 1]
        entry[0][order] = 1 << 11
        for k in range(1, 8):
            for j in range(k):
                entry[j][k + order] = 0
            for j in range(k, 8):
                entry[j][k + order] = entry[j - k][order]
        coef.append(entry)
    return coef


def _qsample(x, scale):
    return int(x / scale + 0.4999999) if x > 0.0 else int(x / scale - 0.4999999)


def _clamp16(e):
    lo, hi = -32768.0, 32767.0
    out = []
    for v in e:
        v = min(hi, max(lo, v))
        out.append(int(v + 0.5) if v > 0.0 else int(v - 0.5))
    return out


def _clip(ix, lo, hi):
    return lo if ix < lo else (hi if ix > hi else ix)


def _s16(v):
    v &= 0xFFFF
    return v - 0x10000 if v >= 0x8000 else v


def _encode_frame(in_buf, state, coef, order, npredictors):
    inb = list(in_buf) + [0] * (16 - len(in_buf))
    llevel, ulevel = -8, 7

    def run(p, quantized):
        pred = [0] * 16
        inv = [0] * 16
        errf = [0.0] * 16
        st = list(state)
        for i in range(order):
            inv[i] = st[16 - order + i]
        for i in range(8):
            pred[i] = _inner_product(order + i, coef[p][i], inv)
            inv[i + order] = inb[i] - pred[i]
            errf[i] = float(inv[i + order])
        for i in range(order):
            inv[i] = pred[8 - order + i] + inv[8 + i]
        for i in range(8):
            pred[8 + i] = _inner_product(order + i, coef[p][i], inv)
            inv[i + order] = inb[8 + i] - pred[8 + i]
            errf[8 + i] = float(inv[i + order])
        return pred, errf

    # pick predictor with lowest error L2 norm
    best, opt = 1e30, 0
    for p in range(npredictors):
        _, errf = run(p, False)
        se = sum(v * v for v in errf)
        if se < best:
            best, opt = se, p

    _, errf = run(opt, False)
    ie = _clamp16(errf)
    mx = 0
    for v in ie:
        if abs(v) > abs(mx):
            mx = v
    for scale in range(13):
        if llevel <= mx <= ulevel:
            break
        mx = int(mx / 2)               # C: int division truncates toward zero
    else:
        scale = 12

    save_state = list(state)
    scale -= 1
    n_iter = 0
    ix = [0] * 16
    new_state = list(state)
    while True:
        n_iter += 1
        max_clip = 0
        scale += 1
        if scale > 12:
            scale = 12
        st = list(save_state)
        inv = [0] * 16
        out_state = [0] * 16
        for i in range(order):
            inv[i] = st[16 - order + i]
        for i in range(8):
            p = _inner_product(order + i, coef[opt][i], inv)
            se = float(inb[i]) - float(p)
            ix[i] = _qsample(se, 1 << scale)
            cv = _s16(_clip(ix[i], llevel, ulevel)) - ix[i]
            max_clip = max(max_clip, abs(cv))
            ix[i] += cv
            inv[i + order] = ix[i] * (1 << scale)
            out_state[i] = p + inv[i + order]
        for i in range(order):
            inv[i] = out_state[8 - order + i]
        for i in range(8):
            p = _inner_product(order + i, coef[opt][i], inv)
            se = float(inb[8 + i]) - float(p)
            ix[8 + i] = _qsample(se, 1 << scale)
            cv = _s16(_clip(ix[8 + i], llevel, ulevel)) - ix[8 + i]
            max_clip = max(max_clip, abs(cv))
            ix[8 + i] += cv
            inv[i + order] = ix[8 + i] * (1 << scale)
            out_state[8 + i] = p + inv[i + order]
        new_state = out_state
        if not (max_clip >= 2 and n_iter < 2):
            break

    state[:] = new_state
    header = ((scale & 0xF) << 4) | (opt & 0xF)
    out = bytearray([header])
    for i in range(0, 16, 2):
        out.append(((ix[i] & 0xF) << 4) | (ix[i + 1] & 0xF))
    return bytes(out)


# --------------------------------------------------------------------------- #
# 80-bit IEEE 754 extended (sample rate field)                               #
# --------------------------------------------------------------------------- #
def _ext80(value):
    if value <= 0:
        return b"\x00" * 10
    import math
    m, e = math.frexp(value)           # value = m * 2**e, 0.5 <= m < 1
    exp = e - 1 + 16383
    mant = int(m * 2.0 * (1 << 63))     # 64-bit mantissa, explicit leading 1
    return struct.pack(">H", exp) + struct.pack(">Q", mant)


def _read_ext80(b):
    exp = struct.unpack(">H", b[:2])[0] & 0x7FFF
    mant = struct.unpack(">Q", b[2:10])[0]
    if exp == 0 and mant == 0:
        return 0
    return mant * (2.0 ** (exp - 16383 - 63))


# --------------------------------------------------------------------------- #
# .aifc container                                                            #
# --------------------------------------------------------------------------- #
_COMP_NAME = b"VADPCM ~4-1"
_CODE_NAME = b"VADPCMCODES"


def encode(samples, sample_rate, table=None):
    """Encode PCM `samples` (mono s16 ints) to a VADPCM AIFF-C byte string with
    the fixed chunk layout FGM.asm expects."""
    if table is None:
        order, npredictors, table = tabledesign(samples)
    else:
        order, npredictors, table = table
    if order != 2 or npredictors != 4:
        raise AudioError(
            f"encode: FGM.asm layout needs order==2, npredictors==4 "
            f"(got {order}, {npredictors})")

    coef = _build_coef_table(order, npredictors, table)
    state = [0] * 16
    n = len(samples)
    body = bytearray()
    pos = 0
    while pos < n:
        nsam = min(16, n - pos)
        body += _encode_frame(samples[pos:pos + nsam], state, coef,
                              order, npredictors)
        pos += nsam
    if len(body) % 2:
        body += b"\x00"
    n_frames = len(body) * 16 // 9

    # -- COMM ------------------------------------------------------------- #
    comm = struct.pack(
        ">hHHh", 1, (n_frames >> 16) & 0xFFFF, n_frames & 0xFFFF, 16)
    comm += _ext80(sample_rate)
    comm += b"VAPC"
    comm += bytes([len(_COMP_NAME)]) + _COMP_NAME          # even -> no pad
    comm_chunk = b"COMM" + struct.pack(">I", len(comm)) + comm

    # -- INST (all zero) ------------------------------------------------- #
    inst_chunk = b"INST" + struct.pack(">I", 20) + b"\x00" * 20

    # -- APPL / VADPCMCODES -------------------------------------------- #
    codes = bytearray()
    for p in range(npredictors):
        for j in range(order):
            for k in range(8):
                codes += struct.pack(">h", _s16(table[p][j][k]))
    appl = b"stoc" + bytes([len(_CODE_NAME)]) + _CODE_NAME
    appl += struct.pack(">hhh", 1, order, npredictors) + bytes(codes)
    appl_chunk = b"APPL" + struct.pack(">I", len(appl)) + appl

    # -- SSND --------------------------------------------------------- #
    ssnd = struct.pack(">ii", 0, 0) + bytes(body)
    ssnd_chunk = b"SSND" + struct.pack(">I", len(ssnd)) + ssnd

    payload = b"AIFC" + comm_chunk + inst_chunk + appl_chunk + ssnd_chunk

    # FORM ckSize: NOT the real (compressed) file size. src/FGM.asm derives a
    # sound's auto length (`add_sound(... -1)`) as `read32 @0x4 / 177`, and the
    # 1P announcer name-delay (character/processor.py) as `read32 @0x4 / 375`.
    # Every stock .aifc carries the *uncompressed* PCM size here - the
    # pre-VADPCM AIFF's FORM size, which the SDK vadpcm_enc copies through
    # without rewriting (~= 2*numFrames + a small AIFF header). Writing the true
    # compressed size (~0.55x that) makes auto length come out short and the
    # sound audibly cuts off partway. 46 = a minimal AIFF header (COMM 18B +
    # SSND 16B + formType 4B + 8); the exact value is noise to `//177`.
    form_size = 2 * n_frames + 46
    out = b"FORM" + struct.pack(">I", form_size) + payload

    # Guard the offsets FGM.asm hard-codes.
    assert out[0x70:0x74] != b"" and len(out) > 0x100, "short aifc"
    if out[0x52:0x56] != b"APPL":
        raise AudioError("aifc layout drift: APPL not at 0x52")
    if out[0xF0:0xF4] != b"SSND":
        raise AudioError("aifc layout drift: SSND not at 0xF0")
    return out


def wav_to_aifc(wav_path, aifc_path, sample_rate=None):
    """Convert a 16-bit PCM WAV to <aifc_path>. If `sample_rate` is given the
    samples are nearest-neighbour resampled to it (and it is written into the
    COMM chunk); otherwise the WAV's own rate is used. Returns the rate used."""
    samples, wav_rate = read_wav(wav_path)
    rate = sample_rate or wav_rate
    if sample_rate and sample_rate != wav_rate:
        out_n = round(len(samples) * sample_rate / wav_rate)
        samples = [samples[min(len(samples) - 1, i * wav_rate // sample_rate)]
                   for i in range(out_n)]
    data = encode(samples, rate)
    with open(aifc_path, "wb") as f:
        f.write(data)
    return rate


# --------------------------------------------------------------------------- #
# Decoder (tests / round-trip verification)                                   #
# --------------------------------------------------------------------------- #
def _iter_chunks(payload):
    i = 0
    while i + 8 <= len(payload):
        cid = payload[i:i + 4]
        size = struct.unpack(">I", payload[i + 4:i + 8])[0]
        body = payload[i + 8:i + 8 + size]
        yield cid, body
        i += 8 + size + (size & 1)


def decode_aifc(data):
    """Decode a VADPCM AIFF-C back to (samples[int], sample_rate)."""
    if data[:4] != b"FORM" or data[8:12] != b"AIFC":
        raise AudioError("not an AIFF-C FORM")
    payload = data[12:]
    order = npredictors = None
    raw_table = None
    sample_rate = 0
    n_frames = 0
    body = b""
    for cid, b in _iter_chunks(payload):
        if cid == b"COMM":
            _, fh, fl, _ss = struct.unpack(">hHHh", b[:8])
            n_frames = (fh << 16) | fl
            sample_rate = int(round(_read_ext80(b[8:18])))
        elif cid == b"APPL" and b[:4] == b"stoc":
            plen = b[4]
            off = 5 + plen + (1 - (plen & 1))
            if b[5:5 + plen] != _CODE_NAME:
                continue
            _ver, order, npredictors = struct.unpack(">hhh", b[off:off + 6])
            off += 6
            raw_table = []
            for _p in range(npredictors):
                rows = []
                for _j in range(order):
                    rows.append(list(struct.unpack(
                        ">8h", b[off:off + 16])))
                    off += 16
                raw_table.append(rows)
        elif cid == b"SSND":
            body = b[8:]
    if raw_table is None:
        raise AudioError("no VADPCMCODES chunk")

    coef = _build_coef_table(order, npredictors, raw_table)
    out = []
    state = [0] * 16
    for fp in range(0, len(body) - 8, 9):
        frame = body[fp:fp + 9]
        if len(frame) < 9:
            break
        header = frame[0]
        scale = 1 << (header >> 4)
        opt = header & 0xF
        ix = [0] * 16
        for i in range(8):
            c = frame[1 + i]
            hi, lo = c >> 4, c & 0xF
            ix[2 * i] = (hi if hi <= 7 else hi - 16) * scale
            ix[2 * i + 1] = (lo if lo <= 7 else lo - 16) * scale
        outp = [0] * 16
        for j in range(2):
            inv = [0] * (order + 8)
            for i in range(8):
                inv[i + order] = ix[j * 8 + i]
            if j == 0:
                for i in range(order):
                    inv[i] = state[16 - order + i]
            else:
                for i in range(order):
                    inv[i] = outp[j * 8 - order + i]
            for i in range(8):
                outp[i + j * 8] = _inner_product(order + 8, coef[opt][i], inv)
        state = outp
        out.extend(outp)
    if n_frames:
        out = out[:n_frames]
    return [_s16(max(-32767, min(32767, v))) for v in out], sample_rate


# --------------------------------------------------------------------------- #
# CLI: python3 -m smashremix_extra.audio.vadpcm in.wav out.aifc [rate]        #
#      python3 -m smashremix_extra.audio.vadpcm --decode in.aifc out.wav      #
#      python3 -m smashremix_extra.audio.vadpcm --check some.aifc             #
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 4 and sys.argv[1] == "--decode":
        r = aifc_to_wav(sys.argv[2], sys.argv[3])
        print(f"{sys.argv[2]} -> {sys.argv[3]} ({r} Hz)")
    elif len(sys.argv) >= 3 and sys.argv[1] == "--check":
        import math
        raw = open(sys.argv[2], "rb").read()
        sam, rate = decode_aifc(raw)
        enc = encode(sam, rate)
        sam2, _ = decode_aifc(enc)
        n = min(len(sam), len(sam2))
        sig = sum(x * x for x in sam[:n]) or 1
        err = sum((sam[i] - sam2[i]) ** 2 for i in range(n)) or 1e-9
        assert enc[0x52:0x56] == b"APPL" and enc[0xF0:0xF4] == b"SSND"
        print(f"{sys.argv[2]}: {rate} Hz, {len(sam)} samples, "
              f"re-encode SNR {10 * math.log10(sig / err):.1f} dB, "
              f"{len(raw)} -> {len(enc)} bytes, layout OK")
    elif len(sys.argv) >= 3:
        rate = int(sys.argv[3]) if len(sys.argv) > 3 else None
        used = wav_to_aifc(sys.argv[1], sys.argv[2], rate)
        print(f"{sys.argv[1]} -> {sys.argv[2]} ({used} Hz)")
    else:
        print(__doc__)
        sys.exit(1)
