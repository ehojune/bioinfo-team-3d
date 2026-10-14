#!/usr/bin/env python3
"""Pure-Python animated GIF encoder for the README demo (dev-only, standard library only).

Reads PNG screenshots (8-bit RGB or RGBA, not interlaced), builds one shared 256-colour palette with
median cut, LZW-compresses every frame and writes a looping GIF89a. Used by scripts/record_demo_gif.cjs;
it can also be run alone:

    python scripts/gif_encode.py frames.json out.gif [--max-mb 5]

frames.json: {"frames": [{"png": "01-start.png", "ms": 1100}, ...]} with paths relative to the JSON file.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import zlib
from collections import Counter
from pathlib import Path


# --- PNG reading -------------------------------------------------------------------------------

def read_png(path):
    """Return (width, height, rgb) where rgb is bytes of width*height*3 (alpha is dropped)."""
    data = Path(path).read_bytes()
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError(f'{path}: not a PNG file')
    pos, chunks = 8, []
    while pos < len(data):
        length, kind = struct.unpack('>I4s', data[pos:pos + 8])
        chunks.append((kind, data[pos + 8:pos + 8 + length]))
        pos += 12 + length
    header = dict(chunks)[b'IHDR']
    width, height, depth, color, _, _, interlace = struct.unpack('>IIBBBBB', header)
    if depth != 8 or color not in (2, 6) or interlace:
        raise ValueError(f'{path}: only 8-bit RGB/RGBA PNG without interlacing is supported '
                         f'(depth={depth}, color type={color}, interlace={interlace})')
    bpp = 3 if color == 2 else 4
    raw = zlib.decompress(b''.join(body for kind, body in chunks if kind == b'IDAT'))
    pixels = unfilter(raw, width, height, bpp)
    if bpp == 4:
        rgb = bytearray(width * height * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = pixels[0::4], pixels[1::4], pixels[2::4]
        return width, height, bytes(rgb)
    return width, height, bytes(pixels)


def unfilter(raw, width, height, bpp):
    stride = width * bpp
    out = bytearray(stride * height)
    previous = bytearray(stride)
    pos = 0
    for y in range(height):
        kind = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        if kind == 1:  # Sub
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif kind == 2:  # Up
            line = bytearray([(a + b) & 255 for a, b in zip(line, previous)])
        elif kind == 3:  # Average
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 255
        elif kind == 4:  # Paeth
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = previous[i]
                c = previous[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 255
        elif kind != 0:
            raise ValueError(f'unknown PNG filter type {kind}')
        out[y * stride:(y + 1) * stride] = line
        previous = line
    return out


# --- palette ------------------------------------------------------------------------------------

def build_palette(histogram, size=256):
    """Median cut over a {0xRRGGBB: pixel count} histogram.

    Returns (palette, index_of): palette is a list of (r, g, b); index_of maps every input colour to
    the palette slot of the box it ended up in.
    """
    boxes = [sorted(histogram.items())]
    while len(boxes) < size:
        best, best_score = None, 0
        for number, box in enumerate(boxes):
            if len(box) < 2:
                continue
            extent = max(_extent(box, shift) for shift in (16, 8, 0))
            score = extent * sum(count for _, count in box)
            if score > best_score:
                best, best_score = number, score
        if best is None:
            break
        box = boxes.pop(best)
        shift = max((16, 8, 0), key=lambda s: _extent(box, s))
        box.sort(key=lambda item: (item[0] >> shift) & 255)
        half, running, cut = sum(count for _, count in box) / 2, 0, 1
        for position, (_, count) in enumerate(box[:-1], start=1):
            running += count
            cut = position
            if running >= half:
                break
        boxes.extend([box[:cut], box[cut:]])
    palette, index_of = [], {}
    for number, box in enumerate(boxes):
        total = sum(count for _, count in box)
        mean = [sum(((color >> shift) & 255) * count for color, count in box) / total for shift in (16, 8, 0)]
        palette.append(tuple(int(round(value)) for value in mean))
        for color, _ in box:
            index_of[color] = number
    return palette, index_of


def _extent(box, shift):
    values = [(color >> shift) & 255 for color, _ in box]
    return max(values) - min(values)


def frame_histogram(rgb):
    packed = [(r << 16) | (g << 8) | b for r, g, b in zip(rgb[0::3], rgb[1::3], rgb[2::3])]
    return Counter(packed), packed


# --- GIF writing --------------------------------------------------------------------------------

def lzw_encode(indices, min_code_size=8):
    """GIF-flavoured LZW (variable width codes, clear code on a full table)."""
    clear, end = 1 << min_code_size, (1 << min_code_size) + 1
    init_bits = min_code_size + 1
    out = bytearray()
    state = {'bits': init_bits, 'maxcode': (1 << init_bits) - 1, 'free': end + 1, 'buf': 0, 'count': 0}

    def output(code, reset=False):
        state['buf'] |= code << state['count']
        state['count'] += state['bits']
        while state['count'] >= 8:
            out.append(state['buf'] & 255)
            state['buf'] >>= 8
            state['count'] -= 8
        if state['free'] > state['maxcode'] or reset:
            if reset:
                state['bits'] = init_bits
                state['maxcode'] = (1 << init_bits) - 1
            else:
                state['bits'] += 1
                state['maxcode'] = 4096 if state['bits'] == 12 else (1 << state['bits']) - 1

    table = {}
    output(clear)
    prefix = indices[0]
    free = state['free']
    for value in indices[1:]:
        key = (prefix << 8) | value
        code = table.get(key)
        if code is not None:
            prefix = code
            continue
        state['free'] = free
        output(prefix)
        prefix = value
        if free < 4096:
            table[key] = free
            free += 1
        else:
            table.clear()
            free = end + 1
            state['free'] = free
            output(clear, reset=True)
    state['free'] = free
    output(prefix)
    output(end)
    if state['count']:
        out.append(state['buf'] & 255)
    return bytes(out)


def _sub_blocks(payload):
    blocks = bytearray()
    for start in range(0, len(payload), 255):
        chunk = payload[start:start + 255]
        blocks.append(len(chunk))
        blocks += chunk
    blocks.append(0)
    return bytes(blocks)


def write_gif(frames, delays_ms, path):
    """frames: list of (width, height, rgb bytes), all the same size. Returns the file size in bytes."""
    width, height = frames[0][0], frames[0][1]
    if any((w, h) != (width, height) for w, h, _ in frames):
        raise ValueError('all frames must have the same size')
    histogram, packed_frames = Counter(), []
    for _, _, rgb in frames:
        counts, packed = frame_histogram(rgb)
        histogram.update(counts)
        packed_frames.append(packed)
    palette, index_of = build_palette(histogram)
    palette += [(0, 0, 0)] * (256 - len(palette))
    out = bytearray(b'GIF89a')
    out += struct.pack('<HHBBB', width, height, 0xF7, 0, 0)
    out += b''.join(bytes(color) for color in palette)
    out += b'\x21\xFF\x0BNETSCAPE2.0\x03\x01\x00\x00\x00'  # loop forever
    for packed, delay in zip(packed_frames, delays_ms):
        indices = bytes([index_of[color] for color in packed])
        out += b'\x21\xF9\x04' + struct.pack('<BHB', 0x04, max(2, round(delay / 10)), 0) + b'\x00'
        out += b'\x2C' + struct.pack('<HHHHB', 0, 0, width, height, 0)
        out += b'\x08' + _sub_blocks(lzw_encode(indices))
    out += b'\x3B'
    Path(path).write_bytes(out)
    return len(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('frames_json')
    parser.add_argument('output')
    parser.add_argument('--max-mb', type=float, default=5.0, help='fail if the GIF is larger (default 5)')
    args = parser.parse_args(argv)
    spec_path = Path(args.frames_json)
    spec = json.loads(spec_path.read_text(encoding='utf-8'))
    frames = [read_png(spec_path.parent / item['png']) for item in spec['frames']]
    size = write_gif(frames, [item['ms'] for item in spec['frames']], args.output)
    seconds = sum(item['ms'] for item in spec['frames']) / 1000
    print(f'{args.output}: {frames[0][0]}x{frames[0][1]}, {len(frames)} frames, {seconds:.1f} s, {size / 1e6:.2f} MB')
    if size > args.max_mb * 1e6:
        print(f'error: larger than {args.max_mb} MB', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
