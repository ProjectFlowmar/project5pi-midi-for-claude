import struct

import pytest

from p5midi.flp import (Clip, FLPError, Note, Project, parse_events, serialize_events)
from conftest import make_flp


def test_roundtrip_is_byte_identical():
    data = make_flp()
    assert serialize_events(*parse_events(data)) == data


def test_varint_lengths_roundtrip():
    for n in (0, 1, 127, 128, 16383, 16384, 300000):
        h, ev = parse_events(make_flp())
        ev.append([224, b'\x01' * n])
        assert parse_events(serialize_events(h, ev))[1][-1][1] == b'\x01' * n


def test_fl2025_event_172_is_three_bytes():
    h = struct.pack('<HHH', 0, 0, 96)
    raw = serialize_events(h, [[199, b'26.1.1.5547\0']])
    raw += bytes([172, 1, 1, 0]) + bytes([192, 4]) + 'ab'.encode('utf-16-le')
    raw = raw[:18] + struct.pack('<I', len(raw) - 22) + raw[22:]
    _, ev = parse_events(raw)
    assert [e[0] for e in ev] == [199, 172, 192]
    assert ev[1][1] == b'\x01\x01\x00'


def test_truncated_file_raises():
    with pytest.raises(FLPError):
        parse_events(make_flp()[:-3])
    with pytest.raises(FLPError):
        parse_events(b'RIFF....')


def test_basic_properties():
    p = Project(*parse_events(make_flp(ppq=96)))
    assert p.ppq == 96 and p.tempo == 120.0 and p.major_version == 20
    p.tempo = 93.5
    assert p.tempo == 93.5
    with pytest.raises(FLPError):
        p.tempo = 1000


def test_channels_stop_at_mixer():
    p = Project(*parse_events(make_flp()))
    assert [c.label for c in p.channels()] == ['Kick', 'Bass', 'Pad']
    assert p.channels()[1].plugin == '3x Osc'


def test_find_channel_exact_plugin_partial_and_id():
    p = Project(*parse_events(make_flp()))
    assert p.find_channel('bass').id == 1
    assert p.find_channel('Sytrus').id == 2
    assert p.find_channel('Kic').id == 0
    assert p.find_channel('2').id == 2
    with pytest.raises(FLPError, match='Channels:'):
        p.find_channel('Violin')


def test_note_pack_unpack():
    n = Note(480, 61, 96, 2, 110, 30)
    assert Note.unpack(n.pack()) == n
    assert len(n.pack()) == 24


def test_add_pattern_goes_before_channels_and_reads_back():
    p = Project(*parse_events(make_flp()))
    pid = p.add_pattern([Note(96, 60, 24, 0), Note(0, 64, 24, 1)], name='verse')
    assert pid == 1 and p.add_pattern([Note(0, 60, 1, 0)]) == 2
    first_chan = next(i for i, e in enumerate(p.events) if e[0] == 64)
    assert max(i for i, e in enumerate(p.events) if e[0] in (65, 224)) < first_chan
    notes = p.patterns()[1]
    assert [n.position for n in notes] == [0, 96]  # sorted
    assert p.pattern_names() == {1: 'verse'}
    assert Project(*parse_events(p.to_bytes())).patterns()[1] == notes


@pytest.mark.parametrize('version,size', [('20.7.0.1702', 32), ('20.99.3000', 60), ('21.2.2.3914', 60),
                                          ('24.1.0.4212', 60), ('24.2.99.4720', 80), ('25.2.5.5319', 80),
                                          ('26.1.1.5547', 88)])
def test_playlist_item_size_by_version(version, size):
    p = Project(*parse_events(make_flp(version=version)))
    assert p.item_size() == size
    clips = [Clip(1, 1, 0, 384), Clip(1, 2, 384, 384), Clip(2, 3, 0, 768)]
    p.set_clips(clips)
    raw = p.events[p._playlist_index()][1]
    assert len(raw) == 3 * size
    got = Project(*parse_events(p.to_bytes())).clips()
    assert sorted((c.pattern, c.track, c.position, c.length) for c in got) == \
        sorted((c.pattern, c.track, c.position, c.length) for c in clips)
    if size > 32:  # running clip number after the offsets
        assert [struct.unpack('<I', raw[i + 32:i + 36])[0] for i in range(0, len(raw), size)] == [1, 2, 3]


def test_item_size_measured_from_existing_items_wins_over_version():
    item80 = struct.pack('<IHHIHH', 0, 20480, 20481, 384, 499, 0) + b'\x00' * 64
    p = Project(*parse_events(make_flp(version='21.0.0', items=item80 * 2)))
    assert p.item_size() == 80
    p.clear_playlist()
    assert p.item_size() == 80  # remembered after clearing


def test_current_arrangement_is_targeted_and_all_cleared():
    p = Project(*parse_events(make_flp(arrangements=2, current=1)))
    p.clear_playlist()
    p.set_clips([Clip(1, 1, 0, 384)])
    pl = [e[1] for e in p.events if e[0] == 233]
    assert pl[0] == b'' and len(pl[1]) == 32


def test_markers_and_track_names():
    p = Project(*parse_events(make_flp(markers=True)))
    p.clear_markers()
    assert not any(e[0] in (148, 33, 34) for e in p.events)
    p.clear_track_names()
    p.set_track_name(2, 'Drums')
    names = [e[1].decode('utf-16-le').rstrip('\0') for e in p.events if e[0] == 239]
    assert names == ['Drums']


def test_set_info_and_song_mode():
    p = Project(*parse_events(make_flp()))
    p.set_info(title='Night Drive', artist='', genre='Trap', comment='hi')
    p.set_song_mode(True)
    d = {e[0]: e[1] for e in p.events}
    assert d[194].decode('utf-16-le').rstrip('\0') == 'Night Drive'
    assert d[206].decode('utf-16-le').rstrip('\0') == 'Trap'
    assert d[9] == b'\x01'
