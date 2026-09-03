from PIL import Image


class ImageMode:
    RGBA5551 = 'RGBA5551'
    IA8 = 'IA8'
    I8 = 'I8'
    I4 = 'I4'


def get_image_data(image_path, expected_width=-1, expected_height=-1):
    '''
        Loads image from image_path and
        returns it as a bytearray along with width and height
    '''
    image = Image.open(image_path).convert('RGBA')

    if expected_width != -1 and expected_height != -1:
        if image.width != expected_width or image.height != expected_height:
            image = image.resize((expected_width, expected_height))

    return list(image.getdata()), image.width, image.height


def rgba5551(_pixels):
    out = bytearray()
    for r, g, b, a in _pixels:
        red = (r & 0xF8) >> 3
        green = (g & 0xF8) >> 3
        blue = (b & 0xF8) >> 3
        alpha_bit = 1 if a > 0 else 0
        high = (red << 3) | ((green & 0x1C) >> 2)
        low = ((green & 0x03) << 6) | (blue << 1) | alpha_bit
        out += bytes([high, low])
    return out


def ia8(_pixels):
    out = bytearray()
    for r, g, b, a in _pixels:
        intensity = (r >> 4) << 4
        alpha = a >> 4
        out.append(intensity | alpha)
    return out


def i8(_pixels):
    out = bytearray()
    for r, g, b, _ in _pixels:
        intensity = (r + g + b) // 3
        out.append(intensity & 0xFF)
    return out


def i4(_pixels):
    '''4-bit intensity, two texels packed per byte (high nibble first).'''
    out = bytearray()
    px = list(_pixels)
    for i in range(0, len(px), 2):
        r, g, b, _ = px[i]
        hi = (((r + g + b) // 3) >> 4) & 0xF
        if i + 1 < len(px):
            r2, g2, b2, _ = px[i + 1]
            lo = (((r2 + g2 + b2) // 3) >> 4) & 0xF
        else:
            lo = 0
        out.append((hi << 4) | lo)
    return out


def interleave(array: bytearray, height: int):
    bytes_per_line = len(array) // height

    for line in range(1, height, 2):  # Start from line 1, every other line
        start = line * bytes_per_line
        end = start + bytes_per_line

        i = start
        while i + 7 < end:
            # Swap 4 bytes with the next 4
            array[i:i+8] = array[i+4:i+8] + array[i:i+4]
            i += 8


def build_header(width, height, data1, data2, pointer1, mode_bytes):
    header = bytearray()
    header.extend([
        0x00, width, 0x00, width,
        0x00, 0x00, 0x00, 0x00,
        (pointer1 >> 8) & 0xFF, pointer1 & 0xFF,
        (data1 >> 8) & 0xFF, data1 & 0xFF,
        0x00, height, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00,
        0x00, width, 0x00, height,
        0x3F, 0x80, 0x00, 0x00,
        0x3F, 0x80, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00,
        0x02, 0x20, 0x12, 0x34,
        0xFF, 0xFF, 0xFF, 0xFF,
        0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x01,
        0x00, 0x01, 0x00, 0x24,
        0x00, height, 0x00, height,
    ])
    header += mode_bytes
    header += b'\x00\x00\xFF\xFF'
    header += bytes([(data2 >> 8) & 0xFF, data2 & 0xFF])
    header += b'\x00' * 16
    return header


# Largest run of texel data that sits behind a single 0xDF marker (matches the
# vanilla data-screen bios, which chunk their pixel data every 0xFF0 bytes).
SEGMENT_DATA_BYTES = 0xFF0


def _patch_previous_pointer(current_file, struct_start):
    '''
        The images in these .bin files form a linked list: the tail of the
        previously appended image points at the first segment node of the next
        one (struct_start + 8).
    '''
    ptr = struct_start // 4 + 2
    if current_file[-0x14] == 0xFF and current_file[-0x13] == 0xFF:
        current_file[-0x14] = (ptr >> 8) & 0xFF
        current_file[-0x13] = ptr & 0xFF
    else:
        current_file[-0xC] = (ptr >> 8) & 0xFF
        current_file[-0xB] = ptr & 0xFF


def build_segmented_header(width, height, seg_data_ptrs, seg_heights,
                           struct_start, mode_bytes, bm_hreal):
    '''
        Builds a multi-segment image struct matching the vanilla data-screen
        bios: one 16-byte node per segment, followed by the shared body.  The
        biography_offsets table entry points at struct_start and the game adds
        0x30 to reach the body, i.e. this only lines up for a 3-segment image.
    '''
    nseg = len(seg_data_ptrs)
    body_start = struct_start + nseg * 0x10
    header = bytearray()

    for i in range(nseg):
        # p1 threads to the next segment node ({p1,d1} pair, i.e. node + 8);
        # the last one points just past the body's constant fields.
        if i < nseg - 1:
            p1 = (struct_start + (i + 1) * 0x10 + 8) // 4
        else:
            p1 = (body_start + 0x34) // 4
        d1 = seg_data_ptrs[i]
        header.extend([
            0x00, width, 0x00, width,
            0x00, 0x00, 0x00, 0x00,
            (p1 >> 8) & 0xFF, p1 & 0xFF,
            (d1 >> 8) & 0xFF, d1 & 0xFF,
            0x00, seg_heights[i], 0x00, 0x00,
        ])

    d2 = struct_start // 4
    marker_lo = 0x40 if nseg > 1 else 0x20
    # Vanilla bios store a display height two rows shorter than the pixel data
    # (0x71 for 115 rows of texels); mirror that.
    disp_height = height - 2 if nseg > 1 else height
    header.extend([
        0x00, 0x00, 0x00, 0x00,
        0x00, width, 0x00, disp_height,
        0x3F, 0x80, 0x00, 0x00,
        0x3F, 0x80, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00,
        0x02, marker_lo, 0x12, 0x34,
        0xFF, 0xFF, 0xFF, 0xFF,
        0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x01,
        0x00, nseg, 0x00, 0x3C,
        0x00, (bm_hreal - 1) & 0xFF, 0x00, bm_hreal & 0xFF,
    ])
    header += mode_bytes
    header += b'\x00\x00\xFF\xFF'
    header += bytes([(d2 >> 8) & 0xFF, d2 & 0xFF])
    header += b'\x00' * 16
    return header


def append_image(input_file, output_file, _pixels, width, height, mode,
                 do_interleave=True) -> int:
    '''
        Appends _pixels data with width/height using mode to the input file
        and writes it to the output file
        Returns a pointer to the added image's data
    '''
    with open(input_file, "rb") as f:
        current_file = bytearray(f.read())

    if mode == ImageMode.RGBA5551:
        image_bytes = rgba5551(_pixels)
        mode_bytes = b'\x00\x02'
    elif mode == ImageMode.IA8:
        image_bytes = ia8(_pixels)
        mode_bytes = b'\x03\x01'
    elif mode == ImageMode.I8:
        image_bytes = i8(_pixels)
        mode_bytes = b'\x04\x01'
    elif mode == ImageMode.I4:
        image_bytes = i4(_pixels)
        mode_bytes = b'\x04\x00'
    else:
        raise ValueError("Invalid image mode")

    bytes_per_row = len(image_bytes) // height
    rows_per_segment = max(1, SEGMENT_DATA_BYTES // bytes_per_row)
    # Only the data-screen I4 textures use the multi-segment / 0xDF-chunked
    # layout; every other caller (portraits, nameplates, ...) keeps the plain
    # single-block format regardless of size.
    if mode == ImageMode.I4:
        nseg = (height + rows_per_segment - 1) // rows_per_segment
    else:
        nseg = 1

    if nseg == 1:
        # Single-segment layout (names, works, special attack captions).
        if do_interleave:
            interleave(image_bytes, height)

        _patch_previous_pointer(
            current_file,
            len(current_file) + 8 + len(image_bytes))

        out = bytearray(current_file)
        out += b'\xDF\x00\x00\x00' + b'\x00' * 4
        data1 = len(out) // 4
        out += image_bytes
        data2 = len(out) // 4
        pointer1 = (len(out) + 60) // 4 + 2
        out += build_header(width, height, data1, data2, pointer1, mode_bytes)
        data_address = data2 * 4
    else:
        # Multi-segment layout (data-screen bios): pixel data is split into
        # <= 0xFF0 byte chunks, each behind its own 0xDF marker.  Each chunk is
        # loaded by its own gDPLoadTextureBlock_*S, which does NOT de-interleave,
        # so every chunk must be word-swapped on its own odd rows.
        # Every bitmap is loaded with tex_height == bmHreal == rows_per_segment,
        # so each segment's data must be a full rows_per_segment rows (the tail
        # segment is zero-padded), while the struct still records the real
        # (drawn) row count per bitmap.
        segments = []
        for i in range(nseg):
            r0 = i * rows_per_segment
            r1 = min(height, r0 + rows_per_segment)
            seg = bytearray(image_bytes[r0 * bytes_per_row:r1 * bytes_per_row])
            seg += b'\x00' * (rows_per_segment * bytes_per_row - len(seg))
            if do_interleave:
                interleave(seg, rows_per_segment)
            segments.append((seg, r1 - r0))

        seg_total = sum(len(s) for s, _ in segments)
        struct_start = len(current_file) + nseg * 8 + seg_total
        _patch_previous_pointer(current_file, struct_start)

        out = bytearray(current_file)
        seg_data_ptrs = []
        seg_heights = []
        for seg, seg_h in segments:
            out += b'\xDF\x00\x00\x00' + b'\x00' * 4
            seg_data_ptrs.append(len(out) // 4)
            out += seg
            seg_heights.append(seg_h)

        assert len(out) == struct_start
        # Plain I4 (bmsiz 0); vanilla ships the bio as i4c (bmsiz 4, a 2bpp
        # runtime-expanded format) but the game renders bmsiz 0 the same way.
        out += build_segmented_header(
            width, height, seg_data_ptrs, seg_heights,
            struct_start, b'\x04\x00', rows_per_segment)
        data_address = struct_start

    with open(output_file, "wb") as f:
        f.write(out)

    if data_address >= 0x3FFFC:
        raise ValueError("Image too large, exceeds addressable space.")

    return data_address


# CLI Support
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Append image to SSB64 .bin file.")
    parser.add_argument("input_bin", help="Input .bin file")
    parser.add_argument("image_file", help="Image file (e.g. PNG)")
    parser.add_argument("output_bin", help="Output .bin file")
    parser.add_argument(
        "--mode", choices=[ImageMode.RGBA5551, ImageMode.IA8, ImageMode.I8], required=True)
    args = parser.parse_args()

    _pixels, w, h = get_image_data(args.image_file)

    append_image(
        args.input_bin, args.output_bin, _pixels, w, h, args.mode
    )
