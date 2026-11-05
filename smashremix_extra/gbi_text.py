#!/usr/bin/env python3
"""
gbi_text.py - F3DEX2 display-list commands <-> readable text.

    decode(w0, w1)  -> "gsDPSetRenderMode(G_RM_AA_ZB_OPA_SURF, G_RM_AA_ZB_OPA_SURF2)"
                       or None when there is no exact macro form (caller keeps hex)
    assemble(text)  -> (w0, w1)
    describe(w0, w1) -> short human note for commands kept as hex (comments only)

Macro names and constants follow the N64 SDK gbi.h, so they can be looked up.
decode() only returns a macro whose assemble() gives back the same 8 bytes, so
export -> import without edits is byte-identical.
"""
import re

# ------------------------------------------------------------------ render mode
AA_EN, Z_CMP, Z_UPD, IM_RD, CLR_ON_CVG = 0x8, 0x10, 0x20, 0x40, 0x80
CVG_DST_CLAMP, CVG_DST_WRAP, CVG_DST_FULL, CVG_DST_SAVE = 0, 0x100, 0x200, 0x300
ZMODE_OPA, ZMODE_INTER, ZMODE_XLU, ZMODE_DEC = 0, 0x400, 0x800, 0xC00
CVG_X_ALPHA, ALPHA_CVG_SEL, FORCE_BL = 0x1000, 0x2000, 0x4000
RM_FLAGS = {"AA_EN": AA_EN, "Z_CMP": Z_CMP, "Z_UPD": Z_UPD, "IM_RD": IM_RD,
            "CLR_ON_CVG": CLR_ON_CVG, "CVG_DST_WRAP": CVG_DST_WRAP,
            "CVG_DST_FULL": CVG_DST_FULL, "CVG_DST_SAVE": CVG_DST_SAVE,
            "ZMODE_INTER": ZMODE_INTER, "ZMODE_XLU": ZMODE_XLU,
            "ZMODE_DEC": ZMODE_DEC, "CVG_X_ALPHA": CVG_X_ALPHA,
            "ALPHA_CVG_SEL": ALPHA_CVG_SEL, "FORCE_BL": FORCE_BL}

# blender inputs (p, a, m, b): out = (p*a + m*b) / (a + b)
_IN, _MEM = 0, 1                  # G_BL_CLR_IN / G_BL_CLR_MEM
_A_IN, _0 = 0, 3                  # G_BL_A_IN / G_BL_0
_1MA, _A_MEM, _1 = 0, 1, 2        # G_BL_1MA / G_BL_A_MEM / G_BL_1
_OPA = (_IN, _A_IN, _MEM, _A_MEM)
_XLU = (_IN, _A_IN, _MEM, _1MA)
_ONLY = (_IN, _0, _IN, _1)

_RM = {   # gbi.h render modes: flags, blender
    "AA_OPA_SURF": (AA_EN | IM_RD | CVG_DST_CLAMP | ZMODE_OPA | ALPHA_CVG_SEL, _OPA),
    "AA_XLU_SURF": (AA_EN | IM_RD | CVG_DST_WRAP | CLR_ON_CVG | FORCE_BL | ZMODE_OPA, _XLU),
    "AA_TEX_EDGE": (AA_EN | IM_RD | CVG_DST_CLAMP | CVG_X_ALPHA | ALPHA_CVG_SEL | ZMODE_OPA, _OPA),
    "AA_ZB_OPA_SURF": (AA_EN | Z_CMP | Z_UPD | IM_RD | CVG_DST_CLAMP | ZMODE_OPA | ALPHA_CVG_SEL, _OPA),
    "AA_ZB_XLU_SURF": (AA_EN | Z_CMP | IM_RD | CVG_DST_WRAP | CLR_ON_CVG | FORCE_BL | ZMODE_XLU, _XLU),
    "AA_ZB_TEX_EDGE": (AA_EN | Z_CMP | Z_UPD | IM_RD | CVG_DST_CLAMP | CVG_X_ALPHA | ALPHA_CVG_SEL | ZMODE_OPA, _OPA),
    "AA_ZB_OPA_DECAL": (AA_EN | Z_CMP | IM_RD | CVG_DST_WRAP | ALPHA_CVG_SEL | ZMODE_DEC, _OPA),
    "AA_ZB_XLU_DECAL": (AA_EN | Z_CMP | IM_RD | CVG_DST_WRAP | CLR_ON_CVG | FORCE_BL | ZMODE_DEC, _XLU),
    "AA_ZB_OPA_INTER": (AA_EN | Z_CMP | Z_UPD | IM_RD | CVG_DST_CLAMP | ALPHA_CVG_SEL | ZMODE_INTER, _OPA),
    "AA_ZB_XLU_INTER": (AA_EN | Z_CMP | IM_RD | CVG_DST_WRAP | CLR_ON_CVG | FORCE_BL | ZMODE_INTER, _XLU),
    "ZB_OPA_SURF": (Z_CMP | Z_UPD | CVG_DST_FULL | ALPHA_CVG_SEL | ZMODE_OPA, _OPA),
    "ZB_XLU_SURF": (Z_CMP | IM_RD | CVG_DST_FULL | FORCE_BL | ZMODE_XLU, _XLU),
    "ZB_OPA_DECAL": (Z_CMP | CVG_DST_FULL | ALPHA_CVG_SEL | ZMODE_DEC, _OPA),
    "ZB_XLU_DECAL": (Z_CMP | IM_RD | CVG_DST_FULL | FORCE_BL | ZMODE_DEC, _XLU),
    "OPA_SURF": (CVG_DST_CLAMP | FORCE_BL | ZMODE_OPA, _ONLY),
    "XLU_SURF": (IM_RD | CVG_DST_FULL | FORCE_BL | ZMODE_OPA, _XLU),
    "TEX_EDGE": (CVG_DST_CLAMP | CVG_X_ALPHA | ALPHA_CVG_SEL | FORCE_BL | ZMODE_OPA, _ONLY),
}


def _c1(f, bl):
    p, a, m, b = bl
    return f | p << 30 | a << 26 | m << 22 | b << 18


def _c2(f, bl):
    p, a, m, b = bl
    return f | p << 28 | a << 24 | m << 20 | b << 16


RM1 = {f"G_RM_{k}": _c1(*v) for k, v in _RM.items()}
RM1.update({"G_RM_PASS": _c1(0, _ONLY), "G_RM_NOOP": 0,
            "G_RM_FOG_SHADE_A": 0xC8000000, "G_RM_FOG_PRIM_A": 0xC4000000})
RM2 = {f"G_RM_{k}2": _c2(*v) for k, v in _RM.items()}
RM2.update({"G_RM_NOOP2": 0})

# --------------------------------------------------------------- other modes
# (op, shift, len, macro, {name: value})
OTHERMODES = [
    (0xE2, 0, 2, "gsDPSetAlphaCompare",
     {"G_AC_NONE": 0, "G_AC_THRESHOLD": 1, "G_AC_DITHER": 3}),
    (0xE2, 2, 1, "gsDPSetDepthSource", {"G_ZS_PIXEL": 0, "G_ZS_PRIM": 4}),
    (0xE3, 4, 2, "gsDPSetAlphaDither",
     {"G_AD_PATTERN": 0, "G_AD_NOTPATTERN": 0x10, "G_AD_NOISE": 0x20,
      "G_AD_DISABLE": 0x30}),
    (0xE3, 6, 2, "gsDPSetColorDither",
     {"G_CD_MAGICSQ": 0, "G_CD_BAYER": 0x40, "G_CD_NOISE": 0x80,
      "G_CD_DISABLE": 0xC0}),
    (0xE3, 8, 1, "gsDPSetCombineKey", {"G_CK_NONE": 0, "G_CK_KEY": 0x100}),
    (0xE3, 9, 3, "gsDPSetTextureConvert",
     {"G_TC_CONV": 0, "G_TC_FILTCONV": 0xA00, "G_TC_FILT": 0xC00}),
    (0xE3, 12, 2, "gsDPSetTextureFilter",
     {"G_TF_POINT": 0, "G_TF_BILERP": 0x2000, "G_TF_AVERAGE": 0x3000}),
    (0xE3, 14, 2, "gsDPSetTextureLUT",
     {"G_TT_NONE": 0, "G_TT_RGBA16": 0x8000, "G_TT_IA16": 0xC000}),
    (0xE3, 16, 1, "gsDPSetTextureLOD", {"G_TL_TILE": 0, "G_TL_LOD": 0x10000}),
    (0xE3, 17, 2, "gsDPSetTextureDetail",
     {"G_TD_CLAMP": 0, "G_TD_SHARPEN": 0x20000, "G_TD_DETAIL": 0x40000}),
    (0xE3, 19, 1, "gsDPSetTexturePersp", {"G_TP_NONE": 0, "G_TP_PERSP": 0x80000}),
    (0xE3, 20, 2, "gsDPSetCycleType",
     {"G_CYC_1CYCLE": 0, "G_CYC_2CYCLE": 0x100000, "G_CYC_COPY": 0x200000,
      "G_CYC_FILL": 0x300000}),
    (0xE3, 23, 1, "gsDPPipelineMode",
     {"G_PM_NPRIMITIVE": 0, "G_PM_1PRIMITIVE": 0x800000}),
]


def _othermode_w0(op, sft, ln):
    return op << 24 | (32 - sft - ln) << 8 | (ln - 1)


GEO_FLAGS = {"G_ZBUFFER": 0x1, "G_SHADE": 0x4, "G_CULL_FRONT": 0x200,
             "G_CULL_BACK": 0x400, "G_FOG": 0x10000, "G_LIGHTING": 0x20000,
             "G_TEXTURE_GEN": 0x40000, "G_TEXTURE_GEN_LINEAR": 0x80000,
             "G_LOD": 0x100000, "G_SHADING_SMOOTH": 0x200000,
             "G_CLIPPING": 0x800000}

# --------------------------------------------------------------- color combiner
_CC_A = {"COMBINED": 0, "TEXEL0": 1, "TEXEL1": 2, "PRIMITIVE": 3, "SHADE": 4,
         "ENVIRONMENT": 5, "1": 6, "NOISE": 7, "0": 15}
_CC_B = {"COMBINED": 0, "TEXEL0": 1, "TEXEL1": 2, "PRIMITIVE": 3, "SHADE": 4,
         "ENVIRONMENT": 5, "CENTER": 6, "K4": 7, "0": 15}
_CC_C = {"COMBINED": 0, "TEXEL0": 1, "TEXEL1": 2, "PRIMITIVE": 3, "SHADE": 4,
         "ENVIRONMENT": 5, "SCALE": 6, "COMBINED_ALPHA": 7, "TEXEL0_ALPHA": 8,
         "TEXEL1_ALPHA": 9, "PRIMITIVE_ALPHA": 10, "SHADE_ALPHA": 11,
         "ENV_ALPHA": 12, "LOD_FRACTION": 13, "PRIM_LOD_FRAC": 14, "K5": 15,
         "0": 31}
_CC_D = {"COMBINED": 0, "TEXEL0": 1, "TEXEL1": 2, "PRIMITIVE": 3, "SHADE": 4,
         "ENVIRONMENT": 5, "1": 6, "0": 7}
_AC_ABD = {"COMBINED": 0, "TEXEL0": 1, "TEXEL1": 2, "PRIMITIVE": 3,
           "SHADE": 4, "ENVIRONMENT": 5, "1": 6, "0": 7}
_AC_C = {"LOD_FRACTION": 0, "TEXEL0": 1, "TEXEL1": 2, "PRIMITIVE": 3,
         "SHADE": 4, "ENVIRONMENT": 5, "PRIM_LOD_FRAC": 6, "0": 7}
# gsDPSetCombineLERP argument order, with (table, word, shift, mask)
_CC_FIELDS = [
    (_CC_A, 0, 20, 0xF), (_CC_B, 1, 28, 0xF), (_CC_C, 0, 15, 0x1F), (_CC_D, 1, 15, 0x7),
    (_AC_ABD, 0, 12, 0x7), (_AC_ABD, 1, 12, 0x7), (_AC_C, 0, 9, 0x7), (_AC_ABD, 1, 9, 0x7),
    (_CC_A, 0, 5, 0xF), (_CC_B, 1, 24, 0xF), (_CC_C, 0, 0, 0x1F), (_CC_D, 1, 6, 0x7),
    (_AC_ABD, 1, 21, 0x7), (_AC_ABD, 1, 3, 0x7), (_AC_C, 1, 18, 0x7), (_AC_ABD, 1, 0, 0x7),
]


def _rev(table, v):
    for k, x in table.items():
        if x == v:
            return k
    return None


# ------------------------------------------------------------------- decoding
def _flags(v, table):
    names = [k for k, f in table.items() if f and v & f == f]
    rest = v
    for k in names:
        rest &= ~table[k]
    if rest:
        names.append(f"0x{rest:X}")
    return " | ".join(names) if names else "0"


def _rgba(w1):
    return f"{w1 >> 24 & 0xFF}, {w1 >> 16 & 0xFF}, {w1 >> 8 & 0xFF}, {w1 & 0xFF}"


def _decode(w0, w1):
    op = w0 >> 24
    simple = {0xE7: "gsDPPipeSync", 0xE6: "gsDPLoadSync", 0xE8: "gsDPTileSync",
              0xE9: "gsDPFullSync", 0xDF: "gsSPEndDisplayList"}
    if op in simple:
        return f"{simple[op]}()"
    if op in (0xFB, 0xF9, 0xF8):
        name = {0xFB: "gsDPSetEnvColor", 0xF9: "gsDPSetBlendColor",
                0xF8: "gsDPSetFogColor"}[op]
        return f"{name}({_rgba(w1)})"
    if op == 0xFA:
        return f"gsDPSetPrimColor({w0 >> 8 & 0xFF}, {w0 & 0xFF}, {_rgba(w1)})"
    if op == 0xE2 and w0 == _othermode_w0(0xE2, 3, 29):
        # several pairs can OR to the same word (flags are shared) - prefer the
        # matching X / X2 pair, then the flag-less cycle-1 modes (PASS, FOG..)
        special = ("G_RM_PASS", "G_RM_NOOP", "G_RM_FOG_SHADE_A", "G_RM_FOG_PRIM_A")
        hits = sorted(
            (0 if n2 == n1 + "2" else 1 if n1 in special else
             2 if v1 & 0xFFFF == v2 & 0xFFFF else 3, n1, n2)
            for n1, v1 in RM1.items() for n2, v2 in RM2.items()
            if v1 | v2 == w1)
        if hits:
            return f"gsDPSetRenderMode({hits[0][1]}, {hits[0][2]})"
        return f"gsDPSetRenderMode(0x{w1:08X})"
    if op in (0xE2, 0xE3):
        for o, sft, ln, name, vals in OTHERMODES:
            if o == op and w0 == _othermode_w0(o, sft, ln):
                n = _rev(vals, w1)
                return f"{name}({n if n else f'0x{w1:X}'})"
        return None
    if op == 0xD9:
        clear = ~w0 & 0xFFFFFF
        return (f"gsSPGeometryMode({_flags(clear, GEO_FLAGS)}, "
                f"{_flags(w1, GEO_FLAGS)})")
    if op == 0xFC:
        args = []
        for table, word, sh, mask in _CC_FIELDS:
            v = ((w0 if word == 0 else w1) >> sh) & mask
            n = _rev(table, v)
            if n is None:
                return None
            args.append(n)
        return (f"gsDPSetCombineLERP({', '.join(args[0:4])},  "
                f"{', '.join(args[4:8])},  {', '.join(args[8:12])},  "
                f"{', '.join(args[12:16])})")
    if op == 0xD7:
        return (f"gsSPTexture(0x{w1 >> 16:04X}, 0x{w1 & 0xFFFF:04X}, "
                f"{w0 >> 11 & 7}, {w0 >> 8 & 7}, {w0 >> 1 & 0x7F})")
    return None


def decode(w0, w1):
    """Macro text for the command, or None (keep it as hex)."""
    try:
        t = _decode(w0, w1)
    except Exception:                                      # noqa: BLE001
        return None
    if t is None:
        return None
    try:
        return t if assemble(t) == (w0, w1) else None
    except ValueError:
        return None


_FMT = {0: "RGBA", 1: "YUV", 2: "CI", 3: "IA", 4: "I"}
_SIZ = {0: "4b", 1: "8b", 2: "16b", 3: "32b"}


def describe(w0, w1):
    """A note for a command kept as hex - for comments only."""
    op = w0 >> 24
    if op == 0x01:
        n = w0 >> 12 & 0xFF
        return f"gsSPVertex: {n} verts -> slot {(w0 >> 1 & 0x7F) - n}"
    if op == 0x05:
        return f"gsSP1Triangle({(w0 >> 16 & 0xFF) // 2},{(w0 >> 8 & 0xFF) // 2}, {(w0 & 0xFF) // 2})"
    if op == 0x06:
        return (f"gsSP2Triangles({(w0 >> 16 & 0xFF) // 2}, {(w0 >> 8 & 0xFF) // 2}, "
                f"{(w0 & 0xFF) // 2},  {(w1 >> 16 & 0xFF) // 2}, "
                f"{(w1 >> 8 & 0xFF) // 2}, {(w1 & 0xFF) // 2})")
    if op == 0xFD:
        return (f"gsDPSetTextureImage {_FMT.get(w0 >> 21 & 7, '?')} "
                f"{_SIZ[w0 >> 19 & 3]} width {(w0 & 0xFFF) + 1}")
    if op == 0xF5:
        return (f"gsDPSetTile {_FMT.get(w0 >> 21 & 7, '?')} {_SIZ[w0 >> 19 & 3]} "
                f"tile {w1 >> 24 & 7} tmem 0x{w0 & 0x1FF:X} pal {w1 >> 20 & 0xF}")
    if op == 0xF2:
        return (f"gsDPSetTileSize tile {w1 >> 24 & 7} "
                f"{((w1 >> 12 & 0xFFF) >> 2) + 1}x{((w1 & 0xFFF) >> 2) + 1}")
    if op == 0xF3:
        return f"gsDPLoadBlock tile {w1 >> 24 & 7} texels {(w1 >> 12 & 0xFFF) + 1}"
    if op == 0xF0:
        return f"gsDPLoadTLUTCmd tile {w1 >> 24 & 7} count {((w1 >> 14) & 0x3FF) + 1}"
    if op == 0xDB:
        return "gsMoveWd (light colour / segment)"
    if op == 0xDE:
        return f"gsSPDisplayList (segment 0x{w1 >> 24:02X})" if w1 >> 24 else "gsSPDisplayList"
    if op == 0xDA:
        return "gsSPMatrix"
    if op == 0xD8:
        return "gsSPPopMatrix"
    if op == 0xE2 or op == 0xE3:
        return f"SetOtherMode_{'L' if op == 0xE2 else 'H'} shift {32 - (w0 >> 8 & 0xFF) - ((w0 & 0xFF) + 1)}"
    return ""


# ------------------------------------------------------------------ assembling
_CALL = re.compile(r"^\s*(\w+)\s*\((.*)\)\s*$")


def _num(tok, names=None, what="value"):
    tok = tok.strip()
    if names is not None and tok in names:
        return names[tok]
    try:
        return int(tok, 0)
    except ValueError:
        raise ValueError(f"unknown {what} {tok!r}") from None


def _or(tok, names, what):
    v = 0
    for part in tok.split("|"):
        v |= _num(part, names, what)
    return v


def _byte(tok):
    v = _num(tok)
    if not 0 <= v <= 255:
        raise ValueError(f"{tok!r} is not 0..255")
    return v


def assemble(text):
    """Macro text -> (w0, w1).  Raises ValueError with a readable reason."""
    m = _CALL.match(text)
    if not m:
        raise ValueError(f"not a macro call: {text!r}")
    name, argstr = m.group(1), m.group(2).strip()
    args = [a.strip() for a in argstr.split(",")] if argstr else []

    def need(n):
        if len(args) != n:
            raise ValueError(f"{name} takes {n} argument(s), got {len(args)}")

    simple = {"gsDPPipeSync": 0xE7, "gsDPLoadSync": 0xE6, "gsDPTileSync": 0xE8,
              "gsDPFullSync": 0xE9, "gsSPEndDisplayList": 0xDF}
    if name in simple:
        need(0)
        return simple[name] << 24, 0
    colors = {"gsDPSetEnvColor": 0xFB, "gsDPSetBlendColor": 0xF9,
              "gsDPSetFogColor": 0xF8}
    if name in colors:
        need(4)
        r, g, b, a = (_byte(x) for x in args)
        return colors[name] << 24, r << 24 | g << 16 | b << 8 | a
    if name == "gsDPSetPrimColor":
        need(6)
        mn, lf, r, g, b, a = (_byte(x) for x in args)
        return 0xFA000000 | mn << 8 | lf, r << 24 | g << 16 | b << 8 | a
    if name == "gsDPSetRenderMode":
        w0 = _othermode_w0(0xE2, 3, 29)
        if len(args) == 1:
            return w0, _or(args[0], RM_FLAGS, "render mode") & 0xFFFFFFFF
        need(2)
        return w0, _num(args[0], RM1, "cycle-1 render mode") | \
            _num(args[1], RM2, "cycle-2 render mode")
    for op, sft, ln, mname, vals in OTHERMODES:
        if name == mname:
            need(1)
            return _othermode_w0(op, sft, ln), _num(args[0], vals, "mode")
    if name in ("gsSPGeometryMode", "gsSPSetGeometryMode", "gsSPClearGeometryMode"):
        if name == "gsSPGeometryMode":
            need(2)
            clear, sets = _or(args[0], GEO_FLAGS, "flag"), _or(args[1], GEO_FLAGS, "flag")
        else:
            need(1)
            v = _or(args[0], GEO_FLAGS, "flag")
            clear, sets = (0, v) if name == "gsSPSetGeometryMode" else (v, 0)
        return 0xD9000000 | (~clear & 0xFFFFFF), sets
    if name == "gsDPSetCombineLERP":
        need(16)
        w = [0xFC000000, 0]
        for tok, (table, word, sh, mask) in zip(args, _CC_FIELDS):
            v = _num(tok, table, "combiner input")
            if v & ~mask:
                raise ValueError(f"combiner input {tok!r} out of range here")
            w[word] |= v << sh
        return w[0], w[1]
    if name == "gsSPTexture":
        need(5)
        s, t, lvl, tile, on = (_num(x) for x in args)
        return (0xD7000000 | (lvl & 7) << 11 | (tile & 7) << 8 | (on & 0x7F) << 1,
                (s & 0xFFFF) << 16 | (t & 0xFFFF))
    raise ValueError(f"unknown macro {name!r} - write the command as hex "
                     f"'XXXXXXXX XXXXXXXX' instead")


_BL_P = {0: "IN", 1: "MEM", 2: "BL", 3: "FOG"}
_BL_A = {0: "A_IN", 1: "A_FOG", 2: "A_SHADE", 3: "0"}
_BL_B = {0: "1MA", 1: "A_MEM", 2: "1", 3: "0"}


def rm_note(w1):
    """Plain-language summary of a render mode word (for comments)."""
    depth = ("depth test+write" if w1 & Z_CMP and w1 & Z_UPD else
             "depth test only" if w1 & Z_CMP else
             "depth write only, NO depth test" if w1 & Z_UPD else
             "NO depth test (draws over everything)")
    zm = {0: "OPA", 0x400: "INTER", 0x800: "XLU", 0xC00: "DEC"}[w1 & 0xC00]

    def bl(sh):
        p, a, m, b = (w1 >> sh + 12 & 3, w1 >> sh + 8 & 3, w1 >> sh + 4 & 3, w1 >> sh & 3)
        return f"{_BL_P[p]}*{_BL_A[a]} + {_BL_P[m]}*{_BL_B[b]}"
    return f"{depth}; zmode {zm}; blend {bl(18)} / {bl(16)}"
