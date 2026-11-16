"""kirby_hat.gltf reading / writing (json + numpy only: also runs inside Blender).

Meshes are plain (no skin): hat_hi / hat_lo belong to Kirby's body joint (game joint 6),
item_hi / item_lo to his hand item joint (game joint 0x11), by name. Positions are in Kirby's
model space (game units, Y up, Kirby facing +Z, at rest). Materials are named; their images are
found by name in textures/ (the glTF references none).
"""
import json
import os
import re

import numpy as np

GLTF = 'kirby_hat.gltf'
MESHES = ('hat_hi', 'hat_lo', 'item_hi', 'item_lo')    # (+ extra hats: hat1_hi, hat1_lo, hat2_hi...)
PART_NAME = re.compile(r'(hat\d*|item)_(hi|lo)$')
HAT_JOINT = 2           # DObjDesc index (game joint 6): Kirby's body, which every hat replaces
ITEM_JOINT = 13         # DObjDesc index (game joint 0x11): what Kirby holds (Fox's / Wolf's blaster)


def base_name(name):
    return re.sub(r'\.\d+$', '', name or '')


def joint_of(name):
    return HAT_JOINT if name.startswith('hat_') else ITEM_JOINT


def _node_matrix(nd):
    if 'matrix' in nd:
        return np.array(nd['matrix'], float).reshape(4, 4).T
    t = np.array(nd.get('translation', [0, 0, 0]), float)
    x, y, z, w = nd.get('rotation', [0, 0, 0, 1])
    s = np.array(nd.get('scale', [1, 1, 1]), float)
    r = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    m = np.eye(4)
    m[:3, :3] = r * s
    m[:3, 3] = t
    return m


def load_meshes(path, names=PART_NAME):
    """-> {name: {pos (n,3), nrm (n,3), uv (n,2), col (n,4), tris: [(material, (i, j, k))]}}: the
    meshes called one of `names` (default: the hat / item / extra hat names; None: every mesh;
    Blender's '.001' suffixes ignored), node transforms applied."""
    gl = json.load(open(path))
    bufs = [open(os.path.join(os.path.dirname(path), b['uri']), 'rb').read() for b in gl['buffers']]
    ct = {5126: 'f4', 5123: 'u2', 5125: 'u4', 5121: 'u1'}
    nc = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT4': 16}

    def acc(i):
        a = gl['accessors'][i]
        bv = gl['bufferViews'][a['bufferView']]
        dt = np.dtype('<' + ct[a['componentType']])
        n = nc[a['type']]
        start = bv.get('byteOffset', 0) + a.get('byteOffset', 0)
        stride = bv.get('byteStride', 0) or dt.itemsize * n
        raw = bufs[bv['buffer']]
        out = np.array([np.frombuffer(raw, dt, n, start + k * stride) for k in range(a['count'])], float)
        if a.get('normalized'):
            out = out / np.iinfo(dt).max
        return out.reshape(a['count'], n) if n > 1 else out.reshape(-1)
    mats = [base_name(m.get('name', f'm{i}')) for i, m in enumerate(gl.get('materials', []))]
    nodes = gl.get('nodes', [])
    parent = {c: i for i, nd in enumerate(nodes) for c in nd.get('children', [])}

    def world(i):
        m = _node_matrix(nodes[i])
        return world(parent[i]) @ m if i in parent else m
    out = {}
    for i, nd in enumerate(nodes):
        name = base_name(nd.get('name', ''))
        if 'mesh' not in nd or (names is not None and not (names.match(name) if hasattr(names, 'match')
                                                              else name in names)):
            continue
        if name in out:
            raise SystemExit(f'{path}: two meshes called {name}')
        W = world(i)
        N = np.linalg.inv(W[:3, :3]).T
        pos, nrm, uv, col, tris = [], [], [], [], []
        for prim in gl['meshes'][nd['mesh']]['primitives']:
            at = prim['attributes']
            base = sum(len(p) for p in pos)
            P = acc(at['POSITION'])
            pos.append(P @ W[:3, :3].T + W[:3, 3])
            n = acc(at['NORMAL']) if 'NORMAL' in at else np.tile([0.0, 1.0, 0.0], (len(P), 1))
            n = n @ N.T
            nrm.append(n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-9))
            uv.append(acc(at['TEXCOORD_0']) if 'TEXCOORD_0' in at else np.zeros((len(P), 2)))
            uv_names = [k for k in sorted(at) if k.startswith('TEXCOORD_') and k != 'TEXCOORD_0']
            if len(uv_names) >= 2:           # blend_to_gltf.py's exact vertex colors (N64_RG / N64_BA)
                rg, ba = acc(at[uv_names[-2]]), acc(at[uv_names[-1]])
                col.append(np.stack([rg[:, 0], 1 - rg[:, 1], ba[:, 0], 1 - ba[:, 1]], 1))
            else:
                col.append(np.ones((len(P), 4)))
            idx = acc(prim['indices']).astype(int).reshape(-1, 3) if 'indices' in prim else \
                np.arange(len(P)).reshape(-1, 3)
            mat = mats[prim['material']] if 'material' in prim else 'default'
            tris += [(mat, tuple(int(x) + base for x in t)) for t in idx]
        out[name] = dict(pos=np.vstack(pos), nrm=np.vstack(nrm), uv=np.vstack(uv), col=np.vstack(col), tris=tris)
    return out


def write_gltf(path, meshes, material_colors=None):
    """meshes {name: {pos, nrm, uv, tris}} (Kirby's model space) -> kirby_hat.gltf + .bin."""
    material_colors = material_colors or {}
    names = []
    for m in meshes.values():
        for mat, _ in m['tris']:
            if mat not in names:
                names.append(mat)
    buf = bytearray()
    views, accs = [], []

    def add(arr, comp, typ, target=None, minmax=False):
        arr = np.ascontiguousarray(arr)
        while len(buf) % 4:
            buf.append(0)
        views.append(dict(buffer=0, byteOffset=len(buf), byteLength=arr.nbytes, **({'target': target} if target else {})))
        buf.extend(arr.tobytes())
        a = dict(bufferView=len(views) - 1, componentType=comp, count=len(arr), type=typ)
        if minmax:
            a['min'], a['max'] = arr.min(0).tolist(), arr.max(0).tolist()
        accs.append(a)
        return len(accs) - 1
    nodes, gmeshes = [], []
    for name in [n for n in MESHES if n in meshes] + sorted(n for n in meshes if n not in MESHES):
        m = meshes[name]
        nrm = np.asarray(m['nrm'], float)
        nrm = nrm / np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-9)
        attrs = dict(POSITION=add(np.asarray(m['pos'], '<f4'), 5126, 'VEC3', 34962, True),
                     NORMAL=add(nrm.astype('<f4'), 5126, 'VEC3', 34962),
                     TEXCOORD_0=add(np.asarray(m['uv'], '<f4'), 5126, 'VEC2', 34962))
        prims = []
        for mat in names:
            idx = np.array([t for mm, t in m['tris'] if mm == mat], '<u4').reshape(-1)
            if len(idx):
                prims.append(dict(attributes=attrs, indices=add(idx, 5125, 'SCALAR', 34963), material=names.index(mat)))
        gmeshes.append(dict(name=name, primitives=prims))
        nodes.append(dict(name=name, mesh=len(gmeshes) - 1))
    gl = dict(asset=dict(version='2.0', generator='ssb64_model kirby_hat'),
              scene=0, scenes=[dict(nodes=list(range(len(nodes))))], nodes=nodes, meshes=gmeshes,
              materials=[dict(name=n, pbrMetallicRoughness=dict(
                  baseColorFactor=[float(x) for x in (list(material_colors.get(n) or (1, 1, 1)) + [1])[:4]],
                  metallicFactor=0.0)) for n in names],
              buffers=[dict(uri=os.path.splitext(os.path.basename(path))[0] + '.bin', byteLength=len(buf))],
              bufferViews=views, accessors=accs)
    open(os.path.splitext(path)[0] + '.bin', 'wb').write(buf)
    open(path, 'w').write(json.dumps(gl, indent=1))
