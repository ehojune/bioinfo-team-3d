"""Golden wire vectors + fake sender + receiver source contract; no PowerShell/.NET execution."""
import io
import json
import struct
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from labhq import windows_broker as broker
from tests.test_windows_broker import world
from tests.test_windows_broker_phases import CREATE, FIRST_PHASE, shell, frames, PHASES

LIMIT = 1024 * 1024
FLAGS = 0x01000608


def data():
    return {'command':'C:\\python.exe -I -S "C:\\a b.py"', 'cwd':'C:\\fixture', 'flags':FLAGS,
            'environment':['FAKE=값😀"\\=value']}


def wire(payload):
    return struct.pack('<I', len(payload)) + payload


def reference_receive(stream):
    """Independent binary/JSON reference, NOT execution of the PowerShell receiver."""
    def exact(count):
        parts = bytearray()
        while len(parts) < count:
            part = stream.read(count - len(parts))
            if not part:
                raise ValueError('truncated frame')
            parts.extend(part)
        return bytes(parts)
    length, = struct.unpack('<I', exact(4))
    if not 0 < length <= LIMIT:
        raise ValueError('length rejected')
    obj = json.loads(exact(length).decode('utf-8', errors='strict'))
    if (not isinstance(obj, dict) or set(obj) != {'command','cwd','flags','environment'}
            or any(not isinstance(obj[k],str) or not obj[k] or '\0' in obj[k] for k in ('command','cwd'))
            or type(obj['flags']) is not int or obj['flags'] != FLAGS
            or not isinstance(obj['environment'],list)
            or any(not isinstance(x,str) or x.find('=') < 1 or '\0' in x for x in obj['environment'])):
        raise ValueError('shape rejected')
    return obj


def test_sender_frame_is_fixed_little_endian_ascii_safe_utf8():
    obj = data()
    frame = broker._request_frame(obj)
    expected = json.dumps(obj,ensure_ascii=True).encode('utf-8')
    assert frame[:4] == struct.pack('<I',len(expected)) and frame[4:] == expected
    assert expected.isascii() and reference_receive(io.BytesIO(frame)) == obj


def test_request_budget_has_a_precise_boundary_and_no_silent_truncation():
    obj = data(); obj['environment'] = ['FAKE=']
    overhead = len(json.dumps(obj,ensure_ascii=True).encode('utf-8'))
    obj['environment'][0] += 'A' * (LIMIT - overhead)
    frame = broker._request_frame(obj)
    assert len(frame) == LIMIT + 4 and reference_receive(io.BytesIO(frame)) == obj
    obj['environment'][0] += 'A'
    with pytest.raises(broker.BrokerFailure,match='request_too_large'):
        broker._request_frame(obj)


def test_large_quoted_unicode_command_cwd_and_environment_construction_fit():
    components = ['中' * 198 + ' b'] * 158
    cwd = '\\\\?\\C:\\' + '\\'.join(components)
    assert all(len(part.encode('utf-16-le')) // 2 <= 255 for part in components)
    obj = {'command':broker.subprocess.list2cmdline(['C:\\python.exe','-I','-S',cwd+'\\script.py']),
           'cwd':cwd, 'flags':FLAGS,
           'environment':['FIRST='+'中'*32700,'SECOND='+'😀'*16350]}
    assert len(obj['command'].encode('utf-16-le')) // 2 < 32767
    assert len(obj['cwd'].encode('utf-16-le')) // 2 < 32767
    frame = broker._request_frame(obj)
    assert len(frame) <= LIMIT+4 and reference_receive(io.BytesIO(frame)) == obj


@pytest.mark.parametrize('raw',[b'',b'\x01',b'\x01\0',b'\x01\0\0',struct.pack('<I',0),
    struct.pack('<I',LIMIT+1),struct.pack('<I',20)+b'{}',wire(b'\xff'),wire(b'{'),
    wire(b'[]'),wire(b'{}'),wire(json.dumps({**data(),'flags':True}).encode()),
    wire(json.dumps({**data(),'environment':['=bad']}).encode())],
    ids=['empty','header1','header2','header3','zero','oversize','short-payload','utf8','json','array','shape','flags','env'])
def test_receiver_reference_rejects_invalid_vectors(raw):
    with pytest.raises((ValueError,UnicodeError)):
        reference_receive(io.BytesIO(raw))


def test_receiver_reference_reads_exactly_one_frame_without_eof_probe():
    frame = wire(json.dumps(data()).encode('utf-8'))
    class Chunked:
        pos = 0
        def read(self,count):
            assert self.pos < len(frame), 'extra EOF probe'
            result = frame[self.pos:self.pos+min(count,3)]
            self.pos += len(result)
            return result
    stream = Chunked()
    assert reference_receive(stream) == data() and stream.pos == len(frame)
    class HeaderOnly:
        calls = []
        def read(self,count):
            self.calls.append(count)
            assert len(self.calls)==1, 'oversize payload was read/allocated'
            return struct.pack('<I',LIMIT+1)
    stream = HeaderOnly()
    with pytest.raises(ValueError,match='length rejected'):
        reference_receive(stream)
    assert stream.calls == [4]


def test_receiver_source_binds_raw_stream_exact_reads_strict_utf8_and_guards_before_create():
    script = broker.WMI_SCRIPT
    assert broker.MAX_REQUEST_BYTES == LIMIT and str(LIMIT) in script
    markers = [script.index(FIRST_PHASE)] + [script.index("Write-Phase '"+p+"'") for p in PHASES[1:]]
    assert markers == sorted(markers)
    positions = [script.index(x) for x in ('[Console]::OpenStandardInput()', '.ReadUInt32()',
        '$length -lt 1 -or $length -gt', '.ReadBytes(', '$payload.Length -ne',
        'GetString($payload)', 'ConvertFrom-Json', 'CreateInstance()', '.Create($r.command')]
    assert positions == sorted(positions)
    assert '[Text.UTF8Encoding]::new($false, $true)' in script
    assert '[byte[]]$payload = $reader.ReadBytes([int]$length)' in script
    assert script.count('.ReadUInt32()') == script.count('.ReadBytes(') == 1
    assert 'ReadToEnd' not in script and '.ReadByte(' not in script and 'Peek(' not in script
    assert "[Console]::Out.Write('{\"code\":-1,\"pid\":0}')" in script
    assert script.index('$r.flags -ne') < script.index('CreateInstance()')
    assert script.index('$entry.IndexOf') < script.index('CreateInstance()')


def test_create_sends_only_framed_stdin_and_rejects_oversize_before_any_spawn(shell,tmp_path,monkeypatch):
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=frames(len(PHASES))+b'{"code":0,"pid":222}'))
    monkeypatch.setattr(broker.subprocess,'run',run)
    environment = {'FAKE':'PRIVATE-值😀'}
    assert broker._create(['C:\\python.exe','C:\\a b.py'], tmp_path, environment) == 222
    args,kw = run.call_args
    payload = reference_receive(io.BytesIO(kw['input']))
    assert payload['environment'] == ['FAKE=PRIVATE-值😀']
    assert payload['flags'] == FLAGS and payload['command'] == broker.subprocess.list2cmdline(['C:\\python.exe','C:\\a b.py'])
    assert 'PRIVATE' not in str(args) and 'env' not in kw
    assert kw['timeout']==broker.PROVIDER_TIMEOUT and kw['stderr'] is broker.subprocess.DEVNULL
    run.reset_mock()
    ticks = iter([70.0, 80.0])
    monkeypatch.setattr(broker.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(broker.BrokerFailure,match='request_too_large') as caught:
        broker._create(['C:\\python.exe','C:\\script.py'],tmp_path,{'FAKE':'PRIVATE'*(LIMIT//7+1)})
    run.assert_not_called()
    assert 'PRIVATE' not in str(caught.value)


def test_oversized_launch_never_opens_a_pid_or_acks_and_cleans_uncommitted_control(world,monkeypatch):
    monkeypatch.setattr(broker,'MAX_REQUEST_BYTES',64)
    run=Mock(side_effect=AssertionError('PowerShell must not start'))
    monkeypatch.setattr(broker.subprocess,'run',run)
    monkeypatch.setattr(broker,'_powershell',lambda:world.state/'log')
    world.create.side_effect=CREATE
    with pytest.raises(broker.BrokerFailure,match='request_too_large'):
        broker.launch(world.command,state=world.state,role='gateway-supervisor',**world.options)
    world.create.assert_called_once(); run.assert_not_called()
    world.native.open_process.assert_not_called(); world.native.k.TerminateProcess.assert_not_called()
    assert not list((world.state/'.launch').iterdir())
