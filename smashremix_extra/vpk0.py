"""
Pure-Python VPK0 encoder, matching the format used by SSB64 (HAL Laboratory).

Format (from https://github.com/tehzz/vpk0):
  "vpk0"          4 bytes  magic
  size            4 bytes  decompressed size, big-endian
  Then a bitstream (MSB-first within each byte):
    method        8 bits   0 = OneSample, 1 = TwoSample
    offset tree            Huffman tree for offset bit-widths
    length tree            Huffman tree for match-length bit-widths
    encoded data           flag(0=literal,1=match) + payload per token

Tree encoding:
  Leaf  → 0 + 8-bit value (the number of bits to read for the actual offset/length)
  Node  → 1
  End   → 1  (when the parse stack has fewer than 2 entries)
  Empty tree → single 1 bit

LZSS parameters (default, matching nvpktool / SSB64):
  window   4096 bytes
  min_len  2 bytes
  max_len  255 bytes

The decoder already exists in SSB.py (class VPK). This module provides compress().
"""

import heapq
from collections import Counter
from typing import Dict, List, Optional, Tuple, Union

# ---------------------------------------------------------------------------
# Bit-level writer
# ---------------------------------------------------------------------------


class _BitWriter:
    """Accumulates bits MSB-first and produces a bytes object."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._byte = 0
        self._fill = 0  # bits written into current byte

    def write_bit(self, bit: int) -> None:
        self._byte = (self._byte << 1) | (1 if bit else 0)
        self._fill += 1
        if self._fill == 8:
            self._buf.append(self._byte)
            self._byte = 0
            self._fill = 0

    def write_bits(self, value: int, n: int) -> None:
        """Write n bits of value, MSB first. No-op for n=0."""
        for i in range(n - 1, -1, -1):
            self.write_bit((value >> i) & 1)

    def flush(self) -> bytes:
        """Pad the last partial byte with zeros and return all bytes."""
        if self._fill:
            self._byte <<= (8 - self._fill)
            self._buf.append(self._byte)
            self._byte = 0
            self._fill = 0
        return bytes(self._buf)


# ---------------------------------------------------------------------------
# LZSS
# ---------------------------------------------------------------------------

# A token is either a literal byte (int) or a (offset, length) back-reference.
_Token = Union[int, Tuple[int, int]]


def _bit_width(n: int) -> int:
    """Number of bits needed to represent n (0 → 0)."""
    return n.bit_length()


def _lzss(data: bytes, window: int = 4096, min_len: int = 2) -> List[_Token]:
    """
    Greedy LZSS with a 3-gram hash table for candidate lookup.

    Matches can wrap (run-length style): data[start + k % off] for k ≥ off.
    Returns a list of literal ints or (offset, length) tuples.
    """
    n = len(data)
    result: List[_Token] = []
    pos = 0
    # 3-gram → list of start positions (newest last, capped at 64)
    htable: Dict[bytes, List[int]] = {}

    while pos < n:
        best_off = 0
        best_len = 0
        window_start = max(0, pos - window)

        if pos + 2 < n:
            key = data[pos:pos + 3]
            candidates = htable.get(key)
            if candidates:
                for start in reversed(candidates):
                    if start < window_start:
                        break
                    off = pos - start
                    length = 0
                    max_match = min(n - pos, 255)
                    while (length < max_match
                           and data[pos + length] == data[start + length % off]):
                        length += 1
                    if length > best_len:
                        best_len = length
                        best_off = off
                    if best_len == 255:
                        break

        # Register every position inside the matched run (or just current pos)
        for i in range(max(1, best_len) if best_len >= min_len else 1):
            p = pos + i
            if p + 2 < n:
                k = data[p:p + 3]
                lst = htable.get(k)
                if lst is None:
                    htable[k] = [p]
                else:
                    if len(lst) >= 64:
                        lst.pop(0)
                    lst.append(p)

        if best_len >= min_len:
            result.append((best_off, best_len))
            pos += best_len
        else:
            result.append(data[pos])
            pos += 1

    return result


# ---------------------------------------------------------------------------
# Huffman tree
# ---------------------------------------------------------------------------

# Internal tree representation: leaf = ('L', value) | node = ('N', left, right)
_TreeNode = Union[Tuple, int]


def _build_huffman(freqs: Counter) -> Optional[_TreeNode]:
    """Build a Huffman tree from symbol → frequency counts. Returns None if empty."""
    if not freqs:
        return None
    ctr = 0
    heap: List[Tuple] = []
    for sym, freq in sorted(freqs.items()):
        heapq.heappush(heap, (freq, ctr, ('L', sym)))
        ctr += 1
    while len(heap) > 1:
        f1, _, n1 = heapq.heappop(heap)
        f2, _, n2 = heapq.heappop(heap)
        heapq.heappush(heap, (f1 + f2, ctr, ('N', n1, n2)))
        ctr += 1
    return heap[0][2]


def _get_codes(tree: Optional[_TreeNode]) -> Dict[int, Tuple[int, int]]:
    """
    Return {symbol: (huffman_code, code_bit_length)}.

    For a single-leaf tree the code is (0, 0) - zero bits are written
    for the Huffman path; the decoder reads the leaf value directly.
    """
    if tree is None:
        return {}
    codes: Dict[int, Tuple[int, int]] = {}
    if tree[0] == 'L':
        codes[tree[1]] = (0, 0)
        return codes

    def _walk(node: _TreeNode, code: int, depth: int) -> None:
        if node[0] == 'L':
            codes[node[1]] = (code, depth)
        else:
            _walk(node[1], code << 1,        depth + 1)  # left  = 0
            _walk(node[2], (code << 1) | 1,  depth + 1)  # right = 1

    _walk(tree, 0, 0)
    return codes


def _write_tree(bw: _BitWriter, tree: Optional[_TreeNode]) -> None:
    """
    Write a Huffman tree into the bitstream using the VPK0 encoding:
      leaf  → 0 + 8-bit value
      node  → 1
      end   → 1  (the terminating bit after all entries)
    An empty tree is just a single terminating 1 bit.
    """
    if tree is None:
        bw.write_bit(1)
        return

    def _emit(node: _TreeNode) -> None:
        if node[0] == 'L':
            bw.write_bit(0)
            bw.write_bits(node[1], 8)
        else:
            _emit(node[1])
            _emit(node[2])
            bw.write_bit(1)

    _emit(tree)
    bw.write_bit(1)  # terminator


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compress(data: bytes) -> bytes:
    """
    VPK0-compress data (Method 0 / OneSample).

    Returns a bytes object starting with the 8-byte "vpk0" header followed
    by the compressed bitstream.  The result is decompressible by SSB.py's
    VPK.dec_file() and by the SSB64 game itself.
    """
    if not data:
        # Trivial: empty input → header with size=0, empty trees, no data
        bw = _BitWriter()
        bw.write_bits(0, 8)   # method = 0
        bw.write_bit(1)       # empty offset tree
        bw.write_bit(1)       # empty length tree
        return b"vpk0" + (0).to_bytes(4, "big") + bw.flush()

    # ── 1. LZSS pass ──────────────────────────────────────────────────────
    tokens = _lzss(data)

    # ── 2. Collect bit-width frequencies ──────────────────────────────────
    off_freqs: Counter = Counter()
    len_freqs: Counter = Counter()
    for tok in tokens:
        if isinstance(tok, tuple):
            off, ln = tok
            off_freqs[_bit_width(off)] += 1
            len_freqs[_bit_width(ln)] += 1

    # ── 3. Build Huffman trees ─────────────────────────────────────────────
    off_tree = _build_huffman(off_freqs)
    len_tree = _build_huffman(len_freqs)

    off_codes = _get_codes(off_tree)
    len_codes = _get_codes(len_tree)

    # ── 4. Write bitstream ─────────────────────────────────────────────────
    bw = _BitWriter()

    bw.write_bits(0, 8)              # method byte = 0 (OneSample)
    _write_tree(bw, off_tree)        # offset Huffman tree
    _write_tree(bw, len_tree)        # length Huffman tree

    for tok in tokens:
        if isinstance(tok, int):
            bw.write_bit(0)          # literal flag
            bw.write_bits(tok, 8)
        else:
            off, ln = tok
            bw.write_bit(1)          # match flag

            off_bw = _bit_width(off)
            off_code, off_code_len = off_codes[off_bw]
            bw.write_bits(off_code, off_code_len)
            bw.write_bits(off, off_bw)

            ln_bw = _bit_width(ln)
            ln_code, ln_code_len = len_codes[ln_bw]
            bw.write_bits(ln_code, ln_code_len)
            bw.write_bits(ln, ln_bw)

    header = b"vpk0" + len(data).to_bytes(4, "big")
    return header + bw.flush()
