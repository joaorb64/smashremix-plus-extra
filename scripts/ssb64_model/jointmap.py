"""Removing / adding fighter joints: everything the game indexes by joint, renumbered.

A fighter's joints are its DObjDesc array (game joint id g = DObjDesc index + 4; 0..3 are TopN,
TransN, XRotN, YRotN). Removing one shifts every joint after it down, and the game finds its
data per joint in many places, all of which must follow:

  character.bin   the DObjDesc arrays (hi, lo) and the per-joint material / costume script
                  arrays (import_fighter.py builds them compacted)
  main.bin        the model part container (FTModelPartDesc*[joint]), the electric skeleton
                  tables ({dl, flags}[joint]) - moved here (Relocator: the pointer chains and
                  main_reqlist.txt follow); joint ids and bit masks of the attributes
                  (jointattrs.py: remap_attrs) and the hurtboxes (gamedata.apply_hurtboxes)
  animations      every animation file is an array with one entry per joint, in the order the
                  game walks the joint tree (figatree.py): the removed joints' entries go
                  (remap_animation; the scripts are kept byte for byte). The base character's
                  animations become the character's own files (anim_import.remap_all).
  shield pose     its neutral DObjDesc array, its 8 sector arrays and the guard on / off
                  animations inside it: entries removed in place (remap_shield_pose; the
                  motion table points into the file by offset, so nothing else moves)
  moveset         hitbox / effect / hurtbox / model part events name joints (7-bit ids): not
                  rewritten - moveset_remap.py lists the actions whose scripts name renumbered joints

Only whole subtrees can go (a joint whose children stay would need its transform pushed down
into them: not supported). Hidden part joints can't be removed.
"""
import struct

import numpy as np

import model_files as MF

JOINT_COMMON_START = MF.JOINT_COMMON_START


class JointMap:
    """The new DObjDesc order: order[k] = the old index at new index k, or a temporary index
    >= n_old for a joint added in Blender (added[t] = dict(parent (old / temporary index), t
    (its translation from the parent, game units), setup, animlock)). Old joints not in it:
    removed."""

    def __init__(self, n_old, order=None, added=None):
        self.n_old = n_old
        self.order = list(range(n_old)) if order is None else list(order)
        self.added = dict(added or {})
        self.new = {x: k for k, x in enumerate(self.order)}
        self.removed = {i for i in range(n_old) if i not in self.new}
        self.n_new = len(self.order)
        self.moved, self.moved_rest, self.joints_old = {}, {}, []
        self.notes = {}                  # moved joint -> animations whose motion it can't keep exactly

    def __bool__(self):
        return self.order != list(range(self.n_old)) or bool(self.moved)

    def idx(self, i):
        """DObjDesc index (old, or a temporary one of an added joint) -> new (None: removed)."""
        if i < 0:
            return i
        return self.new.get(i)

    def game(self, g):
        """Game joint id (old) -> new (TopN..YRotN and negative ids, like -2 = the light item
        joint in moveset events, stay)."""
        if g < JOINT_COMMON_START:
            return g
        n = self.idx(g - JOINT_COMMON_START)
        return None if n is None else n + JOINT_COMMON_START

    def old_game(self, gn):
        """New game joint id -> the old one (an added joint: its temporary id)."""
        if gn < JOINT_COMMON_START:
            return gn
        return self.order[gn - JOINT_COMMON_START] + JOINT_COMMON_START

    def describe(self):
        out = []
        if self.removed:
            out.append('removed ' + ', '.join(f'joint_{i:02d}' for i in sorted(self.removed)))
        if self.added:
            out.append('added ' + ', '.join(f'joint_{self.new[t]:02d}' for t in sorted(self.added, key=self.new.get)))
        if self.moved:
            out.append('moved ' + ', '.join(
                f'joint_{self.new[x]:02d} (under joint_{self.new[p]:02d})' for x, p in sorted(self.moved.items())))
        return '; '.join(out)

    def renumbered(self):
        """[(old, new)] of the old joints whose index changes."""
        return [(i, self.new[i]) for i in range(self.n_old) if i in self.new and self.new[i] != i]

    # -- the new joint tree (for the animations' / shield pose's entry order)
    def parent_of(self, x):
        """A joint's parent after the change (old / temporary index; None: the root)."""
        if x in self.moved:
            return self.moved[x]
        if x < self.n_old:
            return self.joints_old[x].parent
        return self.added[x]['parent']

    def depth_of(self, x):
        p = self.parent_of(x)
        return 0 if p is None else self.depth_of(p) + 1

    def tree_data(self, f, setup_new, hidden_new=None):
        """shieldpose.tree_data of the fighter after the change. setup_new: the new setup_parts
        words; hidden_new: the new hidden part table ([root, parent, init, kind], new ids; default:
        the file's, renumbered)."""
        import shieldpose as SP
        import jointattrs as JAT
        old = SP.tree_data(f)
        depth = {k: self.depth_of(x) for k, x in enumerate(self.order)}
        setup = [[k + 4, depth[k]] for k in range(self.n_new) if JAT.bit(setup_new, k)]
        hidden = [[r, p, kd] for r, p, _, kd in hidden_new] if hidden_new is not None else \
            [[self.game(r), self.game(p), kd] for r, p, kd in old['hidden']]
        return dict(setup=setup, hidden=hidden, all=[[k + 4, depth[k]] for k in range(self.n_new)]), depth

    def joint_order(self, flags):
        """New game ids in the order an animation with these flags covers them."""
        import shieldpose as SP
        kids = SP.tree_from_data(self.tree, flags & 0xFFFFFFE0)
        top = kids.get(0) or []
        return SP.walk(kids, top[0]) if top else []


def make(f, removed):
    """JointMap for fighter f without the DObjDesc entries `removed`."""
    n = len(f.details['hi']['joints'])
    return make_order(f, [i for i in range(n) if i not in set(removed)], {})


def mat_of(v):
    """Track values {rx..sz} -> the joint's local 4x4 (T * Rz Ry Rx * S)."""
    M = np.eye(4)
    M[:3, :3] = MF.rot_zyx(v['rx'], v['ry'], v['rz']) * np.array([v['sx'], v['sy'], v['sz']])
    M[:3, 3] = [v['tx'], v['ty'], v['tz']]
    return M


def make_order(f, order, added, setup_new=None, parents=None, modes=None):
    """JointMap for fighter f (model_files.load) with the new DObjDesc order (old indices, and
    temporary ones >= the old count for added joints: added {t: dict(parent, head (game-space
    world position), setup, animlock)}). Checks: removed joints are whole branches and not hidden
    parts; every joint keeps its parent (no reparenting) - the DObjDesc depths must say it."""
    joints = f.details['hi']['joints']
    n_old = len(joints)
    jm = JointMap(n_old, order, added)
    jm.joints_old = joints
    # joints moved to another parent (Blender: Set Parent / Alt+P)
    for x, p in (parents or {}).items():
        if x < n_old and x not in jm.removed and p != joints[x].parent:
            jm.moved[x] = p
    import jointattrs as JAT_
    hp_ = f.ptr_main(f.attr + JAT_.A_HIDDEN)
    hidden_roots = {r - 4 for r, _, _, _ in (JAT_.read_hidden(f.main if hp_[0] == 'main' else f.char, hp_[1]) if hp_ else [])}

    def in_hidden_branch(x):
        while x is not None:
            if x in hidden_roots:
                return True
            x = jm.parent_of(x) if x in jm.moved or x >= n_old else joints[x].parent
        return False
    for x, p in jm.moved.items():
        if p is None or joints[x].parent is None:
            raise SystemExit(f'joint_{x:02d}: the root joint stays the root')
        if x in hidden_roots or in_hidden_branch(p):
            raise SystemExit(f'joint_{x:02d}: moving hidden parts / joints into a hidden part\'s branch is not supported')
        q = p
        while q is not None:
            if q == x:
                raise SystemExit(f'joint_{x:02d} can\'t go under its own descendant')
            q = jm.parent_of(q)
    for j in joints:
        if j.parent in jm.removed and j.index not in jm.removed:
            raise SystemExit(f'joint_{j.parent:02d} can\'t be removed: its child joint_{j.index:02d} stays '
                             '(remove the whole branch, or move the child to another parent first)')
    import jointattrs as JAT
    hp = f.ptr_main(f.attr + JAT.A_HIDDEN)
    hidden = JAT.read_hidden(f.main if hp[0] == 'main' else f.char, hp[1]) if hp else []
    for k, (root, parent, _, _) in enumerate(hidden):
        if root - JOINT_COMMON_START in jm.removed:
            raise SystemExit(f'joint_{root - 4:02d} is hidden part {k}: removing hidden part joints is not supported')
    # world rest matrices: old ones, and the added joints' (their parent's rotation, at their head)
    W = {j.index: j.world for j in joints}
    for t in sorted(added, key=jm.new.get):
        a = added[t]
        p = a['parent']
        if p is not None and p not in W:
            raise SystemExit(f'added joint {t}: its parent {p} comes after it')
        PW = W[p] if p is not None else np.eye(4)
        head = np.array(list(a['head']) + [1.0])
        local = np.linalg.inv(PW) @ head
        a['t'] = [float(x) for x in local[:3]]
        M = np.eye(4)
        M[:3, 3] = local[:3]
        W[t] = PW @ M
    # moved joints: the same place (world rest), now relative to the new parent
    import jointanim as JA
    for x, p in jm.moved.items():
        jm.moved_rest[x] = {k: float(v) for k, v in JA.decompose(np.linalg.inv(W[p]) @ W[x]).items()}
    # FOLLOW (default): its motion relative to its rest carries over (it moves with the new parent);
    # KEEP: re-expressed to move in the world as before
    jm.moved_mode = {x: (modes or {}).get(x, 'FOLLOW') for x in jm.moved}
    jm.worlds = W
    # parents implied by the new depths must be the joints' parents
    setup = setup_new
    if setup is None:
        sp = f.ptr_main(f.attr + JAT.A_SETUP)
        setup = list(struct.unpack_from('>2I', f.main if sp[0] == 'main' else f.char, sp[1])) if sp else [0xFFFFFFFF] * 2
        setup = remap_bits(setup, jm, {t: True for t in added})
    jm.tree, depth = jm.tree_data(f, setup)
    last = {}
    for k, x in enumerate(order):
        d = depth[k]
        implied = last.get(d - 1) if d > 0 else None
        px = jm.parent_of(x)
        want = jm.new.get(px) if px is not None else None
        if implied != want:
            raise SystemExit(f'new joint_{k:02d}: the DObjDesc order makes joint_{implied} its parent, not '
                             f'joint_{want} (joints are stored depth first: a new joint goes after its parent\'s '
                             'other children)')
        last[d] = k
    return jm


def remap_bits(words, jm, new_bits=None):
    """setup_parts / animlock words in the new order (added joints: new_bits[t])."""
    bits = [bool((words[i // 32] >> (31 - i % 32)) & 1) for i in range(64)]
    nb = [bits[x] if x < jm.n_old else bool((new_bits or {}).get(x, False)) for x in jm.order]
    nb = (nb + [False] * 64)[:64]
    nw = [0, 0]
    for i, on in enumerate(nb):
        if on:
            nw[i // 32] |= 1 << (31 - i % 32)
    return nw


# ---------------------------------------------------------------------------- pointer chains
class Relocator:
    """A relocatable file whose per-joint arrays are rebuilt in the new joint order: in place
    when they don't grow, else at the end of the file (the pointer to the array re-pointed). The
    pointer fields move with their entries; the internal chain (and the external one, with its
    reqlist lines in chain order) are rebuilt. Chains end up in field order."""

    def __init__(self, data, int_head, ext_head=None, ext_lines=None):
        self.d = bytearray(data)
        self.int = dict(MF.chain(bytes(data), int_head)) if int_head < len(data) else {}
        self.ext = list(MF.chain(bytes(data), ext_head).items()) if ext_head is not None and ext_head < len(data) else []
        if ext_lines is not None and len(ext_lines) != len(self.ext):
            raise SystemExit(f'{len(ext_lines)} reqlist lines for {len(self.ext)} external pointers')
        self.ext_lines = list(ext_lines) if ext_lines is not None else [None] * len(self.ext)
        self.fmap = {}
        self.new_int = {}

    def alloc(self, size, align=8):
        self.d.extend(b'\0' * (-len(self.d) % align))
        o = len(self.d)
        self.d.extend(b'\0' * size)
        return o

    def remap(self, base, stride, n, order, ptr_field=None, new_entry=None):
        """The array of n entries (stride bytes) at base, rebuilt as order (old entry index per
        new slot; >= n: a new entry, new_entry(index) -> bytes or zeros). Grows: written at the
        file's end and the internal pointer at ptr_field re-pointed. -> its offset."""
        if list(order) == list(range(n)):
            return base
        old = bytes(self.d[base:base + stride * n])
        dst = base if len(order) <= n else self.alloc(stride * len(order), 4)
        if dst == base:
            self.d[base:base + stride * n] = b'\0' * (stride * n)
        ext_fields = {fld for fld, _ in self.ext}
        pos = {x: k for k, x in enumerate(order)}
        for k, x in enumerate(order):
            if x < n:
                self.d[dst + stride * k:dst + stride * (k + 1)] = old[stride * x:stride * (x + 1)]
            elif new_entry is not None:
                self.d[dst + stride * k:dst + stride * (k + 1)] = new_entry(x)
        for i in range(n):
            for o in range(0, stride, 4):
                fld = base + stride * i + o
                if fld in self.int or fld in ext_fields:
                    self.fmap[fld] = dst + stride * pos[i] + o if i in pos else None
        if dst != base:
            if ptr_field is None or ptr_field not in self.int:
                raise SystemExit(f'array 0x{base:X} grows but its pointer isn\'t an internal one')
            self.int[ptr_field] = dst
        return dst

    def add_ext(self, field, target, line):
        """A new external pointer (its reqlist line goes where the chain order puts it)."""
        self.ext.append((field, target))
        self.ext_lines.append(line)

    def finish(self):
        """-> (data, int_head, ext_head, ext_lines): heads 0x3FFFC when a chain is empty."""
        ints = {}
        for fld, t in self.int.items():
            nf = self.fmap.get(fld, fld)
            if nf is not None:
                ints[nf] = t
        ints.update(self.new_int)
        exts = []
        for (fld, t), line in zip(self.ext, self.ext_lines):
            nf = self.fmap.get(fld, fld)
            if nf is not None:
                exts.append((nf, t, line))
        exts.sort()
        int_head = self._write(sorted(ints.items()))
        ext_head = self._write([(fl, t) for fl, t, _ in exts])
        return bytes(self.d), int_head, ext_head, [ln for _, _, ln in exts]

    def _write(self, items):
        for k, (fld, t) in enumerate(items):
            nxt = items[k + 1][0] // 4 if k + 1 < len(items) else 0xFFFF
            struct.pack_into('>HH', self.d, fld, nxt, t // 4)
        return items[0][0] if items else 0x3FFFC


def main_tables(f, jm, reloc):
    """main.bin's per-joint arrays (Relocator over main.bin): the model part container and the
    electric skeleton tables. -> (notes, {'modelparts': the container's offset now})."""
    notes, where = [], {}
    mp = f.ptr_main(f.attr + MF.A_MODELPARTS)
    if mp is not None and mp[0] == 'main':
        at = reloc.remap(mp[1], 4, jm.n_old, jm.order, f.attr + MF.A_MODELPARTS)
        where['modelparts'] = at
        notes.append('model part container' + (' (moved to the end)' if at != mp[1] else ''))
    sk = f.ptr_main(f.attr + MF.A_SKELETON)
    if sk is not None and sk[0] == 'main':
        for sid in range(1, 4):
            tab = f.ptr_main(sk[1] + 4 * sid)
            if tab is not None and tab[0] == 'main':
                at = reloc.remap(tab[1], 8, jm.n_old, jm.order, sk[1] + 4 * sid)
                notes.append(f'skeleton {sid}' + (' (moved to the end)' if at != tab[1] else ''))
    return notes, where


# ---------------------------------------------------------------------------- joint attributes
def remap_attrs(ja, jm, new_bits=None):
    """A jointattrs dict (game joint ids - added joints: their temporary ids -, bit masks per
    DObjDesc entry) in the new numbering. new_bits: {'setup' / 'animlock': {t: bool}} of the
    added joints."""
    if ja is None:
        return None
    out = dict(ja)

    def g(v, what):
        n = jm.game(v)
        if n is None:
            raise SystemExit(f'{what} is joint_{v - 4:02d}, which is removed: point it at another joint first '
                             '(Armature > N64 Fighter Joints)')
        return n
    out['joints'] = jm.n_new
    for key in ('setup', 'animlock'):
        w = ja.get(key)
        if w is not None:
            out[key] = remap_bits(w, jm, (new_bits or {}).get(key))
    out['effect'] = [g(v, f'effect joint {i}') for i, v in enumerate(ja['effect'])]
    out['item_heavy'] = g(ja['item_heavy'], 'the heavy item joint')
    out['item_light'] = g(ja['item_light'], 'the light item joint')
    foot = dict(ja.get('foot') or {})
    for s in ('r', 'l'):
        if s in foot:
            foot[s] = g(foot[s], f'foot IK {s}')
    out['foot'] = foot
    out['hidden'] = [[g(r, f'hidden part {k}'), g(p, f'hidden part {k} parent'), i, kd]
                     for k, (r, p, i, kd) in enumerate(ja.get('hidden') or [])]
    ts = ja.get('translate_scales')
    if ts is not None:
        nts = ts[:JOINT_COMMON_START] + [ts[x + 4] if x < jm.n_old and x + 4 < len(ts) else [1.0, 1.0, 1.0]
                                         for x in jm.order]
        out['translate_scales'] = (nts + [[1.0, 1.0, 1.0]] * len(ts))[:len(ts)]
    if ja.get('texture_parts') is not None:
        out['texture_parts'] = [[g(t[0], f'texture part {k}') if t[0] >= 4 else t[0], t[1], t[2]]
                                for k, t in enumerate(ja['texture_parts'])]
    if ja.get('access_joint') is not None:
        out['access_joint'] = g(ja['access_joint'], 'the accessory part')
    return out


# ---------------------------------------------------------------------------- animations
def _script_spans(data, arr, ptrs):
    """Each array entry's script: (bytes from its offset to the next script or the file end,
    [(field, target)] pointers inside it, relative to it - 32-bit scripts' jumps)."""
    starts = sorted({t for t in arr if t is not None})
    span = {}
    inner = {f_: t for f_, t in ptrs.items() if f_ >= 4 * len(arr)}
    for k, s in enumerate(starts):
        e = starts[k + 1] if k + 1 < len(starts) else len(data)
        fix = []
        for f_, t in inner.items():
            if s <= f_ < e:
                if not s <= t < e:
                    raise ValueError(f'a script pointer at 0x{f_:X} leads into another script: not supported')
                fix.append((f_ - s, t - s))
        span[s] = (data[s:e], fix)
    return [span[t] if t is not None else None for t in arr]


def remap_entries(f, entries, flags, jm):
    """[per old-order entry] (an animation's array, maybe with spare entries past the joints) ->
    the same in the new joint order (added joints: None)."""
    import figatree as FT
    old_order = FT.joint_order(f, flags)
    by_g = {g: entries[k] for k, g in enumerate(old_order) if k < len(entries)}
    new_order = jm.joint_order(flags)
    out = [by_g.get(jm.old_game(gn)) for gn in new_order]
    return out + list(entries[len(old_order):])          # (spare entries kept)


def remap_animation(f, data, flags, jm, int_head=None, name=None):
    """An animation file of fighter f (the OLD joints) played with these motion flags -> the file
    in the new joint order (removed joints' entries gone, added joints' NULL: they stay at rest;
    scripts kept as they are). Unaffected: the same bytes object."""
    import figatree as FT
    head = FT.first_pointer_head(data) if int_head is None else int_head
    arr, ptrs = FT.pointer_array(data, head)
    new = remap_entries(f, arr, flags, jm)
    if len(arr) < len(FT.joint_order(f, flags)):     # (a short array - not an animation, or the game reads
        new = new[:len(arr)]                         # past it - keeps its length)
    moved_here = [x for x in jm.moved if x + 4 in FT.joint_order(f, flags)] if len(arr) >= len(FT.joint_order(f, flags)) else []
    if new == arr and not moved_here:
        return data
    spans = dict(zip(arr, _script_spans(data, arr, ptrs)))
    out = [spans[t] if t is not None else None for t in new]
    if moved_here:
        new_order = jm.joint_order(flags)
        anim = FT.load(f, data, head, flags)
        anim['name'] = name
        for x in moved_here:
            k = new_order.index(jm.game(x + 4))
            out[k] = reexpress_script(f, jm, x, anim, flags)
    return FT.write_file(out)


def _anim_local(f, jm, anim, x, t):
    """Joint x's local matrix at frame t of an animation (figatree.load; old / temporary index)."""
    import jointanim as JA
    if x >= jm.n_old:
        M = np.eye(4)
        M[:3, 3] = jm.added[x]['t']
        return M
    v = anim['values'].get(f'joint_{x:02d}')
    if v is None:
        return mat_of(JA.rest_values(jm.joints_old[x]))
    return mat_of({k: seq[min(t, len(seq) - 1)] for k, seq in v.items()})


def _relative(f, jm, x, local_of):
    """-> function(t): new-parent^-1 @ old-parent world (from their lowest common ancestor down),
    local_of(z, t): a joint's local at frame t."""
    old_p = jm.joints_old[x].parent
    new_p = jm.moved[x]

    def ancestors(y, par):
        out = [y]
        while y is not None:
            y = par(y)
            out.append(y)
        return out
    old_par = (lambda y: jm.joints_old[y].parent if y < jm.n_old else jm.added[y]['parent'])
    a_old = ancestors(old_p, old_par)
    a_new = ancestors(new_p, jm.parent_of)
    top = next(y for y in a_old if y in a_new)

    def chain(y, par, t):
        path = []
        while y != top:
            path.append(y)
            y = par(y)
        M = np.eye(4)
        for z in reversed(path):
            M = M @ local_of(z, t)
        return M
    return lambda t: np.linalg.inv(chain(new_p, jm.parent_of, t)) @ chain(old_p, old_par, t)


def reexpress_script(f, jm, x, anim, flags=0):
    """Joint x (moved) in an animation: its motion relative to the new parent, as a script in the
    file's format (16-bit; motion flag 0x8: 32-bit, (bytes, its jump pointers)), None: it stays at
    its new rest."""
    import figatree as FT
    import jointanim as JA
    n = anim['frames']
    if jm.moved_mode.get(x) != 'KEEP':
        if f'joint_{x:02d}' not in anim['values']:
            return None                                   # (not animated: it stays at its new rest)
        D = mat_of(jm.moved_rest[x]) @ np.linalg.inv(mat_of(JA.rest_values(jm.joints_old[x])))
        rel = lambda t: D                                 # noqa: E731
    else:
        rel = _relative(f, jm, x, lambda z, t: _anim_local(f, jm, anim, z, t))
    vals = {k: [] for k in JA.EDITABLE}
    prev = None
    worst = 0.0
    for t in range(n):
        want = rel(t) @ _anim_local(f, jm, anim, x, t)
        v = JA.decompose(want, prev)
        prev = v
        worst = max(worst, float(np.abs(mat_of(v)[:3, :3] - want[:3, :3]).max()))
        for k in JA.EDITABLE:
            vals[k].append(float(v[k]))
    if worst > 0.01:                 # (an unevenly scaled parent: keeping the motion needs shear, which a
        jm.notes.setdefault(x, []).append(anim.get('name') or '?')   # joint can't store: the closest pose)
    loop = bool((anim.get('loops') or {}).get(f'joint_{x:02d}'))
    if flags & FT.FLAG_ANIMJOINT:                     # 32-bit scripts (AObjEvent32), like the file's others
        sc, ptrs, _ = JA.build(dict(frames=n - 1, loop=loop, loop_start=0, samples=vals), jm.moved_rest[x])
        return None if sc is None else (sc, ptrs)
    sc, _ = FT.write_joint(vals, jm.moved_rest[x], (), loop)
    return sc


def shield_moved(f, jm, sp):
    """The shield pose's moved joints re-expressed under their new parents, as shieldpose.build_file's
    'blend' input: dict(turn {bone: [local 4x4 per frame 0..360]}, neutral {bone: 4x4}, edited)."""
    import shieldpose as SP
    import jointanim as JA
    tl = SP.timeline(sp)
    pos = {g - 4: i for i, g in enumerate(sp['order']) if g >= 4}

    def named(d):
        return {JA.NAMES[k]: v for k, v in d.items() if k != 3}

    def local_of(z, t):
        if z >= jm.n_old:
            M = np.eye(4)
            M[:3, 3] = jm.added[z]['t']
            return M
        if z in pos:
            return mat_of(named(tl[pos[z]][t] if t is not None else SP.values_of(sp['lookup'][pos[z]])))
        return mat_of(JA.rest_values(jm.joints_old[z]))
    out = dict(turn={}, neutral={}, edited=[])
    for x in jm.moved:
        if x not in pos:
            continue
        nm = f'joint_{x:02d}'
        if jm.moved_mode.get(x) != 'KEEP':
            D = mat_of(jm.moved_rest[x]) @ np.linalg.inv(mat_of(JA.rest_values(jm.joints_old[x])))
            rel = lambda t, D=D: D                        # noqa: E731
        else:
            rel = _relative(f, jm, x, local_of)
        out['turn'][nm] = [[round(float(c), 7) for c in (rel(t) @ local_of(x, t)).flatten()] for t in range(len(tl[pos[x]]))]
        out['neutral'][nm] = [round(float(c), 7) for c in (rel(None) @ local_of(x, None)).flatten()]
        out['edited'] += [['turn', nm], ['neutral', nm]]
    return out


def remap_shield_pose(f, data, int_head, jm, motions=(), look=None, secs=None):
    """The shield-pose file (fighter f's OLD joints) -> (data, int_head, lookup, sectors): the
    neutral DObjDesc array and the 8 sector arrays in the new order (in place when they don't
    grow, else new arrays at the end of the file; an added joint: its rest pose, no scripts).
    Guard animations inside it (motions: [(offset, flags)]): in place, removals only."""
    import shieldpose as SP
    import figatree as FT
    reloc = Relocator(data, int_head)
    if look is None:
        look = f.main_ext.get(f.attr + SP.A_LOOKUP)
    if secs is None:
        secs = [f.main_ext.get(f.attr + SP.A_SECTORS + 4 * k) for k in range(SP.N_SECTORS)]
    old_order = SP.walk(SP.guard_tree(f), 2)
    lookup = SP.read_lookup(data, look)
    n = min(len(old_order), len(lookup))
    kids = SP.tree_from_data(jm.tree)
    new_order = SP.walk(kids, 2)
    pos_old = {g: k for k, g in enumerate(old_order[:n])}
    order = [pos_old.get(jm.old_game(gn), n + 100 + gn) for gn in new_order]   # (>= n: an added joint)
    depth_old = [e['depth'] for e in lookup]

    def depth_of(gn, k_new):
        # an added joint: one deeper than its parent (in this array's own depths)
        par = next(p for p, ch in kids.items() if gn in ch)
        kp = new_order.index(par)
        x = order[kp]
        return (depth_old[x] if x < n else depth_of(par, kp)) + 1

    def new_entry(x):
        gn = x - n - 100
        a = jm.added[jm.old_game(gn) - 4]
        e = struct.pack('>II9f', depth_of(gn, new_order.index(gn)), 0, *a['t'], 0.0, 0.0, 0.0, 1.0, 1.0, 1.0)
        return e
    if order != list(range(n)):
        grow = len(order) > n
        if grow:
            # new lookup (+ end marker) and sector arrays at the end; the old ones stay unused
            la = reloc.alloc(MF.DOBJDESC_SIZE * len(order) + 4, 8)
            old = bytes(data[look:look + MF.DOBJDESC_SIZE * n])
            for k, x in enumerate(order):
                reloc.d[la + MF.DOBJDESC_SIZE * k:la + MF.DOBJDESC_SIZE * (k + 1)] = \
                    old[MF.DOBJDESC_SIZE * x:MF.DOBJDESC_SIZE * (x + 1)] if x < n else new_entry(x)
            struct.pack_into('>I', reloc.d, la + MF.DOBJDESC_SIZE * len(order), MF.DOBJDESC_END)
            new_secs = []
            for arr in secs:
                na = reloc.alloc(4 * len(order), 4)
                for k, x in enumerate(order):
                    if x < n and arr + 4 * x in reloc.int:
                        reloc.new_int[na + 4 * k] = reloc.int[arr + 4 * x]
                new_secs.append(na)
            look, secs = la, new_secs
        else:
            reloc.remap(look, MF.DOBJDESC_SIZE, len(lookup), order + list(range(n, len(lookup))))
            end = look + MF.DOBJDESC_SIZE * (len(order) + len(lookup) - n)
            struct.pack_into('>I', reloc.d, end, MF.DOBJDESC_END)
            for arr in secs:
                reloc.remap(arr, 4, n, order)
    if jm.moved:                       # the moved branches' depths in the neutral array
        base_depth = lookup[0]['depth'] if lookup else 0
        dep = {}

        def walk_d(g, d):
            dep[g] = d
            for c in kids.get(g, []):
                walk_d(c, d + 1)
        walk_d(2, base_depth)
        for k, gn in enumerate(new_order):
            struct.pack_into('>I', reloc.d, look + MF.DOBJDESC_SIZE * k, dep.get(gn, base_depth))
    for off, flags in sorted(set(motions)):
        ord_ = FT.joint_order(f, flags)
        new = remap_entries(f, list(range(len(ord_))), flags, jm)
        if any(x is None for x in new):
            raise SystemExit('the shield-pose file holds guard animations: adding joints to them is not supported')
        reloc.remap(off, 4, len(ord_), new)
    d, ih, _, _ = reloc.finish()
    return d, ih, look, secs


# ---------------------------------------------------------------------------- moveset
# words per motion event (decomp ftmain.c ftMainUpdateMotionEventsDefault; FTMotionEvent enum)
EVENT_WORDS = {3: 5, 4: 5, 7: 2, 12: 2, 13: 2, 31: 4, 34: 2, 36: 2, 38: 4, 39: 4, 46: 2}
LAST_EVENT = 51
END_EVENTS = {0, 35, 36, 37}           # end, return, goto (followed), pause
JUMP_EVENTS = {34, 36, 46}             # subroutine, goto, parallel script: word 2 = a pointer
# 7-bit joint id fields: (shift) per event
JOINT_FIELD = {3: 13, 4: 13, 28: 19, 31: 19, 38: 19, 39: 19, 40: 19}


def _sx7(v):
    return v - 128 if v & 64 else v


def patch_script_events(d, start, jm, ptrs=None, end=None, visit=None, what=''):
    """Motion events from `start` (bytearray d, patched in place): joint ids renumbered. Follows
    subroutine / goto / parallel pointers that are internal (ptrs: the file's internal chain).
    end: stop there (a loose script file). -> [(offset, old, new)] changes."""
    changes = []
    todo = [start]
    visit = set() if visit is None else visit
    while todo:
        o = todo.pop()
        while (end is None or o < end) and o + 4 <= len(d) and o not in visit:
            visit.add(o)
            w = MF.u32(d, o)
            op = w >> 26
            if op > LAST_EVENT:
                raise ValueError(f'{what}0x{o:X}: unknown motion event {op} ({w:08X})')
            if op in JOINT_FIELD:
                sh = JOINT_FIELD[op]
                g = _sx7((w >> sh) & 0x7F)
                n = jm.game(g) if g >= 0 else g
                if n is None:
                    raise SystemExit(f'{what}0x{o:X}: event {op} uses joint_{g - 4:02d}, which is removed '
                                     '(change the move first)')
                if n != g:
                    w = (w & ~(0x7F << sh)) | ((n & 0x7F) << sh)
                    struct.pack_into('>I', d, o, w)
                    changes.append((o, g, n))
            words = EVENT_WORDS.get(op, 1)
            if op in JUMP_EVENTS and ptrs is not None and o + 4 in ptrs:
                todo.append(ptrs[o + 4])
            if op in END_EVENTS and end is None:
                break
            o += 4 * words
    return changes


def patch_moveset(data, int_head, starts, jm, what='moveset'):
    """A moveset file (the base character's: every action's events, entered at `starts` - the
    motion table's command offsets) -> (patched bytes, number of joint ids changed)."""
    d = bytearray(data)
    ptrs = MF.chain(bytes(data), int_head)
    visit, n = set(), 0
    for s in sorted(set(starts)):
        if 0 <= s < len(d):
            n += len(patch_script_events(d, s, jm, ptrs, visit=visit, what=f'{what} '))
    return bytes(d), n


def patch_script_file(data, jm, what=''):
    """A loose moveset .bin (the character's moveset/ folder: events run straight through) ->
    (bytes, changes) or raises ValueError for events it can't read."""
    d = bytearray(data)
    ch = patch_script_events(d, 0, jm, None, end=len(d), what=what)
    return bytes(d), len(ch)
