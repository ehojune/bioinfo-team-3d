"""Offline art-lab contract checks; no browser, server, or network required."""
import base64
import hashlib
import json
from pathlib import Path
import re
import runpy

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / 'labhq/web/lab3d'
VENDOR = ROOT / 'labhq/web/vendor/three'
VENDOR_JS = {
    'build/three.module.js', 'build/three.core.js',
    'examples/jsm/controls/OrbitControls.js',
    'examples/jsm/loaders/GLTFLoader.js',
    'examples/jsm/utils/BufferGeometryUtils.js',
    'examples/jsm/utils/SkeletonUtils.js',
}
URL = re.compile(r'https?://[^\s\x27\x22<>`\\]+', re.I)


def executable_text(text):
    # Exclude /* block comments */, standalone // comments, and inline //
    # comments after whitespace/semicolon. This also removes GLSL comments in
    # the vendored shader templates. Never remove a URL's :// delimiter.
    # Markdown/LICENSE are prose; package metadata URLs are not requests.
    # This is a bounded source audit, not a JS parser or a runtime network test;
    # the browser audit additionally observes requests and CSP blocks remotes.
    text = re.sub(r'/\*[\s\S]*?\*/|(?m:^\s*//.*$)|(?m:(?<=[\s;])//[^\n]*)', '', text)
    return re.sub(r'<!--[\s\S]*?-->', '', text)


def manifest(name):
    source = (LAB / 'src/skins.js').read_text(encoding='utf-8')
    match = re.search(rf'export const {name} = (\[[\s\S]*?\n\]);', source)
    assert match, f'{name}: keep the selection manifest as a JSON literal'
    return json.loads(match[1])


def test_importmap_is_local_and_resolves():
    html = (LAB / 'index.html').read_text(encoding='utf-8')
    maps = re.findall(r'<script type="importmap">([\s\S]*?)</script>', html)
    assert len(maps) == 1
    assert json.loads(maps[0]) == {'imports': {
        'three': '../vendor/three/build/three.module.js',
        'three/addons/': '../vendor/three/examples/jsm/',
    }}
    for value in json.loads(maps[0])['imports'].values():
        assert (LAB / value).exists()
    assert "connect-src 'self' data: blob:" in html


def test_no_external_request_urls():
    for folder in (LAB, VENDOR):
        for file in folder.rglob('*'):
            if not file.is_file() or '__pycache__' in file.parts:
                continue
            # Raster texture bytes contain no request-bearing declarations;
            # glTF/GLB resource URIs are scanned in their JSON instead.
            if file.suffix in {'.md', '.pyc', '.png', '.jpg', '.jpeg', '.webp', '.avif'} or file.name in {'LICENSE', 'VERSION'}:
                continue
            if file.name == 'package.json':
                metadata = json.loads(file.read_text(encoding='utf-8'))
                # Upstream provenance metadata only: never exempt dependencies
                # or executable JS. Preserve the original package.json bytes.
                for key in ('repository', 'bugs', 'homepage', 'funding'):
                    metadata.pop(key, None)
                text = json.dumps(metadata)
            elif file.suffix == '.glb':
                # GLB JSON chunk begins at byte 20; inspect nested resource URIs.
                data = file.read_bytes()
                assert data[:4] == b'glTF'
                length = int.from_bytes(data[12:16], 'little')
                text = data[20:20 + length].decode('utf-8')
            else:
                text = executable_text(file.read_text(encoding='utf-8'))
            urls = set(URL.findall(text))
            if file == VENDOR / 'build/three.core.js':
                # XML namespace passed to createElementNS: an identifier, never
                # fetched. This exact upstream literal is the only code exception.
                urls.discard('http://www.w3.org/1999/xhtml')
            assert not urls, f'{file.relative_to(ROOT)}: {urls}'


@pytest.mark.parametrize('source', [
    "fetch('https://example.invalid/a')",
    'const model = "http://example.invalid/a.glb";',
    '<img src="https://example.invalid/a.png">',
    '/* documentation */ fetch("https://example.invalid/a"); // explanation',
])
def test_url_audit_keeps_request_literals(source):
    assert URL.search(executable_text(source))


def test_vendor_version_and_sha256():
    lines = (VENDOR / 'VERSION').read_text(encoding='utf-8').splitlines()
    assert lines[0] == '0.186.1'
    entries = dict(line.split('  ', 1)[::-1] for line in lines[1:])
    assert len(lines[1:]) == len(entries)
    assert set(entries) == VENDOR_JS == {p.relative_to(VENDOR).as_posix() for p in VENDOR.rglob('*.js')}
    for relative, digest in entries.items():
        assert re.fullmatch('[a-f0-9]{64}', digest)
        assert hashlib.sha256((VENDOR / relative).read_bytes()).hexdigest() == digest
    package = json.loads((VENDOR / 'package.json').read_text(encoding='utf-8'))
    assert package['version'] == lines[0]
    assert package['license'] == 'MIT'
    assert (VENDOR / 'LICENSE').is_file()


def test_skin_ids_match_core_agents():
    ids = {yaml.safe_load(p.read_text(encoding='utf-8'))['id'] for p in (ROOT / 'agents/core').glob('*.yaml')}
    skins = manifest('SKINS')
    assert len(ids) == 11
    assert len(skins) == 12
    assert {s['id'] for s in skins} == ids | {'contract'}
    assert all(s['type'] == 'procedural' for s in skins)
    for model in manifest('MODEL_SKINS'):
        assert model['type'] == 'gltf'
        assert (LAB / model['src']).resolve().is_relative_to((LAB / 'assets').resolve())
        assert (LAB / model['src']).is_file()
        assert set(model['clips']) == {'queued', 'working', 'waiting', 'hibernating', 'done', 'error'}


def test_file_and_document_budgets():
    for folder in (LAB, VENDOR):
        for file in folder.rglob('*'):
            if file.is_file():
                assert file.stat().st_size <= 5 * 1024 * 1024, file
    assert (LAB / 'assets/placeholder.gltf').stat().st_size <= 50 * 1024
    for name, limit in [('README.md', 30), ('SKINS.md', 40)]:
        assert len((LAB / name).read_text(encoding='utf-8').splitlines()) <= limit


def test_placeholder_is_self_contained_and_reproducible():
    text = (LAB / 'assets/placeholder.gltf').read_text(encoding='utf-8')
    model = json.loads(text)
    assert model['asset']['version'] == '2.0'
    assert 'CC0-1.0' in model['asset']['copyright']
    assert not model.get('images')
    for buffer in model['buffers']:
        prefix, encoded = buffer['uri'].split(',', 1)
        assert prefix == 'data:application/octet-stream;base64'
        assert len(base64.b64decode(encoded, validate=True)) == buffer['byteLength']
    for view in model['bufferViews']:
        assert view.get('byteOffset', 0) % 4 == 0
        assert view.get('byteOffset', 0) + view['byteLength'] <= model['buffers'][view['buffer']]['byteLength']
    assert {'anchor_head', 'anchor_hand_l', 'anchor_hand_r'} <= {n.get('name') for n in model['nodes']}
    assert [a['name'] for a in model['animations']] == ['working']
    generate = runpy.run_path(str(LAB / 'assets/generate_placeholder.py'))['generate']
    assert text == generate()
