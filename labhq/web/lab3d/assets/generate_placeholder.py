"""Generate our CC0 paper robot: no downloads, textures, or third-party model."""
import base64
import json
import math
from pathlib import Path
import struct


def generate():
    binary = bytearray()
    views, accessors, meshes = [], [], []

    def accessor(values, width, kind, bounds=False):
        flat = [n for row in values for n in row]
        offset = len(binary)
        binary.extend(struct.pack('<' + 'f' * len(flat), *flat))
        views.append({'buffer': 0, 'byteOffset': offset, 'byteLength': len(flat) * 4})
        a = {'bufferView': len(views) - 1, 'componentType': 5126,
             'count': len(values), 'type': kind}
        if bounds:
            a.update(min=[min(v[i] for v in values) for i in range(width)],
                     max=[max(v[i] for v in values) for i in range(width)])
        accessors.append(a)
        return len(accessors) - 1

    def mesh(boxes):
        positions, normals, colors = [], [], []
        for center, size, color in boxes:
            # Counterclockwise faces viewed from outside; flat paper surfaces.
            for axis in range(3):
                u, v = (axis + 1) % 3, (axis + 2) % 3
                for sign in (-1, 1):
                    corners = []
                    for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                        p = list(center)
                        p[axis] += sign * size[axis] / 2
                        p[u] += a * size[u] / 2
                        p[v] += b * size[v] / 2
                        corners.append(p)
                    indices = (0, 1, 2, 0, 2, 3) if sign == 1 else (0, 2, 1, 0, 3, 2)
                    for i in indices:
                        normal = [0, 0, 0]
                        normal[axis] = sign
                        positions.append(corners[i]); normals.append(normal); colors.append(color)
        meshes.append({'primitives': [{'attributes': {
            'POSITION': accessor(positions, 3, 'VEC3', True),
            'NORMAL': accessor(normals, 3, 'VEC3'),
            'COLOR_0': accessor(colors, 3, 'VEC3')}, 'material': 0}]})
        return len(meshes) - 1

    sage, cream, ink, gold = [.26,.48,.38], [.95,.88,.67], [.035,.08,.065], [.8,.52,.12]
    body = mesh([([0,.75,0],[.64,.9,.44],sage),
                 ([-.20,.15,.03],[.22,.30,.38],cream),([.20,.15,.03],[.22,.30,.38],cream),
                 ([0,.95,.235],[.30,.22,.035],gold)])
    head = mesh([([0,0,0],[.85,.60,.57],cream),
                 ([-.19,.04,.30],[.10,.10,.04],ink),([.19,.04,.30],[.10,.10,.04],ink),
                 ([0,-.14,.30],[.20,.035,.04],sage),([0,.40,0],[.08,.20,.08],gold)])
    hand = mesh([([0,-.25,0],[.20,.50,.23],sage),([0,-.53,.01],[.24,.12,.25],cream)])
    times = accessor([[0],[.25],[.5]], 1, 'SCALAR', True)
    turns = accessor([[math.sin(a/2),0,0,math.cos(a/2)] for a in (-.4,-1.1,-.4)],4,'VEC4')
    doc = {'asset': {'version': '2.0', 'generator': 'labhq generate_placeholder.py',
                     'copyright': 'CC0-1.0; generated for this repository'},
           'scene': 0, 'scenes': [{'nodes': [0]}],
           'nodes': [{'name': 'paper_robot', 'children': [1,2,3,4]},
                     {'mesh': body},
                     {'name': 'anchor_head', 'mesh': head, 'translation': [0,1.50,0]},
                     {'name': 'anchor_hand_l', 'mesh': hand, 'translation': [-.44,1.14,0]},
                     {'name': 'anchor_hand_r', 'mesh': hand, 'translation': [.44,1.14,0]}],
           'materials': [{'name': 'matte_paper', 'pbrMetallicRoughness': {
               'metallicFactor': 0, 'roughnessFactor': .93}}],
           'meshes': meshes, 'animations': [{'name': 'working',
               'samplers': [{'input': times,'output': turns,'interpolation':'LINEAR'}],
               'channels': [{'sampler':0,'target':{'node':3,'path':'rotation'}}]}],
           'buffers': [{'byteLength': len(binary), 'uri': 'data:application/octet-stream;base64,' + base64.b64encode(binary).decode()}],
           'bufferViews': views, 'accessors': accessors}
    return json.dumps(doc, separators=(',', ':')) + '\n'


if __name__ == '__main__':
    Path(__file__).with_name('placeholder.gltf').write_text(generate(), encoding='utf-8', newline='\n')
