"""scripts/gif_encode.py: the dev-only GIF encoder behind the README demo animation.

A small independent GIF/LZW decoder lives here so the encoder is checked against the format, not itself.
"""
from __future__ import annotations

import importlib.util
import random
import struct
import zlib
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('gif_encode', Path(__file__).resolve().parents[1] / 'scripts' / 'gif_encode.py')
gif_encode = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gif_encode)


def make_png(width, height, rgb, kind):
    """A PNG whose scanlines all use one filter type (0 none, 1 sub, 2 up, 3 average, 4 paeth)."""
    stride = width * 3
    rows = [rgb[y * stride:(y + 1) * stride] for y in range(height)]
    raw = bytearray()
    for y, row in enumerate(rows):
        prev = rows[y - 1] if y else bytes(stride)
        raw.append(kind)
        for i in range(stride):
            a = row[i - 3] if i >= 3 else 0
            b = prev[i]
            c = prev[i - 3] if i >= 3 else 0
            if kind == 0:
                predictor = 0
            elif kind == 1:
                predictor = a
            elif kind == 2:
                predictor = b
            elif kind == 3:
                predictor = (a + b) >> 1
            else:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                predictor = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
            raw.append((row[i] - predictor) & 255)

    def chunk(tag, body):
        return struct.pack('>I', len(body)) + tag + body + struct.pack('>I', zlib.crc32(tag + body))

    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(bytes(raw))) + chunk(b'IEND', b''))


def lzw_decode(data, min_code_size):
    """Reference decoder written from the GIF89a specification."""
    clear, end = 1 << min_code_size, (1 << min_code_size) + 1
    size, table, out, previous = min_code_size + 1, None, bytearray(), None
    bits = bitcount = position = 0

    def reset():
        return [bytes([i]) for i in range(clear)] + [b'', b'']

    while True:
        while bitcount < size:
            if position >= len(data):
                raise AssertionError('LZW data ended before the end code')
            bits |= data[position] << bitcount
            bitcount += 8
            position += 1
        code = bits & ((1 << size) - 1)
        bits >>= size
        bitcount -= size
        if code == clear:
            table, size, previous = reset(), min_code_size + 1, None
            continue
        if code == end:
            return bytes(out)
        if previous is None:
            entry = table[code]
        elif code < len(table):
            entry = table[code]
            if len(table) < 4096:
                table.append(table[previous] + entry[:1])
        else:
            entry = table[previous] + table[previous][:1]
            if len(table) < 4096:
                table.append(entry)
        out += entry
        previous = code
        if len(table) == (1 << size) and size < 12:
            size += 1


def read_gif(path):
    data = Path(path).read_bytes()
    assert data[:6] == b'GIF89a' and data[-1:] == b'\x3B'
    width, height, packed, _, _ = struct.unpack('<HHBBB', data[6:13])
    assert packed & 0x80 and (packed & 7) == 7
    palette = [tuple(data[13 + 3 * i:16 + 3 * i]) for i in range(256)]
    pos, delays, frames, looped = 13 + 768, [], [], False
    while data[pos] != 0x3B:
        if data[pos] == 0x21:
            label = data[pos + 1]
            pos += 2
            body = bytearray()
            while data[pos]:
                body += data[pos + 1:pos + 1 + data[pos]]
                pos += 1 + data[pos]
            pos += 1
            if label == 0xFF and bytes(body).startswith(b'NETSCAPE2.0'):
                looped = bytes(body)[-2:] == b'\x00\x00'
            if label == 0xF9:
                delays.append(struct.unpack('<H', bytes(body)[1:3])[0])
        elif data[pos] == 0x2C:
            left, top, w, h, flags = struct.unpack('<HHHHB', data[pos + 1:pos + 10])
            assert (left, top, w, h, flags) == (0, 0, width, height, 0)
            minimum = data[pos + 10]
            pos += 11
            payload = bytearray()
            while data[pos]:
                payload += data[pos + 1:pos + 1 + data[pos]]
                pos += 1 + data[pos]
            pos += 1
            indices = lzw_decode(bytes(payload), minimum)
            assert len(indices) == width * height
            frames.append(indices)
        else:
            raise AssertionError(f'unexpected block {data[pos]:#x}')
    return width, height, palette, frames, delays, looped


@pytest.mark.parametrize('kind', [0, 1, 2, 3, 4])
def test_png_reader_undoes_every_filter(tmp_path, kind):
    rng = random.Random(kind)
    width, height = 17, 9
    rgb = bytes(rng.randrange(256) for _ in range(width * height * 3))
    path = tmp_path / 'frame.png'
    path.write_bytes(make_png(width, height, rgb, kind))
    assert gif_encode.read_png(path) == (width, height, rgb)


def test_png_reader_rejects_what_it_cannot_decode(tmp_path):
    path = tmp_path / 'bad.png'
    path.write_bytes(b'not a png')
    with pytest.raises(ValueError):
        gif_encode.read_png(path)


def synthetic_frame(width, height, seed, colors):
    rng = random.Random(seed)
    palette = [bytes(rng.randrange(256) for _ in range(3)) for _ in range(colors)]
    rgb = bytearray()
    for y in range(height):
        for x in range(width):
            # Mostly flat areas with noise, like a screenshot, but enough variety to fill the LZW table.
            rgb += palette[(x // 9 + y // 7) % colors] if rng.random() < .8 else palette[rng.randrange(colors)]
    return width, height, bytes(rgb)


def test_round_trip_is_exact_when_the_frames_fit_the_palette(tmp_path):
    frames = [synthetic_frame(220, 160, seed, 40) for seed in range(3)]
    out = tmp_path / 'demo.gif'
    size = gif_encode.write_gif(frames, [500, 1200, 30], out)
    assert size == out.stat().st_size
    width, height, palette, decoded, delays, looped = read_gif(out)
    assert (width, height) == (220, 160) and looped
    assert delays == [50, 120, 3]
    for (_, _, rgb), indices in zip(frames, decoded):
        assert bytes(channel for index in indices for channel in palette[index]) == rgb


def test_lzw_survives_table_overflow_and_clear_codes():
    rng = random.Random(7)
    indices = bytes(rng.randrange(256) for _ in range(60000))  # incompressible: the table fills many times
    assert lzw_decode(gif_encode.lzw_encode(indices), 8) == indices
    assert lzw_decode(gif_encode.lzw_encode(bytes([5])), 8) == bytes([5])
    assert lzw_decode(gif_encode.lzw_encode(bytes(100000)), 8) == bytes(100000)


def test_palette_keeps_dominant_colours_exact_and_stays_within_256():
    histogram = {0xFFFFFF: 500000, 0x000000: 90000, 0x264AA1: 40000}
    rng = random.Random(3)
    for _ in range(3000):
        histogram[rng.randrange(1 << 24)] = rng.randrange(1, 6)
    palette, index_of = gif_encode.build_palette(histogram)
    assert len(palette) <= 256
    for color in (0xFFFFFF, 0x000000, 0x264AA1):
        assert palette[index_of[color]] == ((color >> 16) & 255, (color >> 8) & 255, color & 255)


def test_cli_builds_a_gif_from_frames_json_and_enforces_the_size_cap(tmp_path, capsys):
    for number in range(2):
        width, height, rgb = synthetic_frame(60, 40, number, 12)
        (tmp_path / f'{number}.png').write_bytes(make_png(width, height, rgb, 4))
    (tmp_path / 'frames.json').write_text('{"frames":[{"png":"0.png","ms":400},{"png":"1.png","ms":600}]}', encoding='utf-8')
    out = tmp_path / 'out.gif'
    assert gif_encode.main([str(tmp_path / 'frames.json'), str(out)]) == 0
    assert '2 frames, 1.0 s' in capsys.readouterr().out
    assert gif_encode.main([str(tmp_path / 'frames.json'), str(out), '--max-mb', '0.0001']) == 1
