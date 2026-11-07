import json
import struct

import pytest

from p5midi import theory
from p5midi.flp import Project
from p5midi.midi import export_midi
from p5midi.song import SpecError, build


def test_note_names():
    assert theory.note_number('C4') == 60
    assert theory.note_number('F#2') == 42
    assert theory.note_number('Bb3') == 58
    assert theory.note_number(61) == 61
    with pytest.raises(theory.TheoryError):
        theory.note_number('H2')


def test_keys_and_chords():
    assert theory.parse_key('A minor') == (9, 'minor')
    assert theory.parse_key('Ebm') == (3, 'minor')
    assert theory.parse_key('F# dorian') == (6, 'dorian')
    assert theory.chord_from_roman('i', 'A minor', 3) == [57, 60, 64]          # A C E
    assert theory.chord_from_roman('V7', 'C major', 4) == [67, 71, 74, 77]     # G B D F
    assert theory.chord_from_roman('bVII', 'C major', 4) == [70, 74, 77]       # Bb D F
    assert theory.chord_from_roman('VImaj7', 'A minor', 3)[-1] - theory.chord_from_roman('VImaj7', 'A minor', 3)[0] == 11
    assert theory.chord_from_roman('IIsus4', 'C major', 4) == [62, 67, 69]
    with pytest.raises(theory.TheoryError):
        theory.chord_from_roman('X', 'C major')


def test_voice_leading_moves_less_than_root_position():
    roots = [theory.chord_from_roman(s, 'C major', 4) for s in ('I', 'IV', 'V', 'I')]
    led = theory.voice_lead(roots)
    move = lambda cs: sum(abs(a - b) for x, y in zip(cs, cs[1:]) for a, b in zip(sorted(x), sorted(y)))
    assert move(led) < move(roots)
    assert all({n % 12 for n in a} == {n % 12 for n in b} for a, b in zip(led, roots))


def test_grid_parsing():
    assert theory.parse_grid('x..X|o-g.') == [(0, 1, 100), (3, 1, 120), (4, 2, 70), (6, 1, 45)]
    assert theory.grid_steps('x... x...') == 8
    with pytest.raises(theory.TheoryError):
        theory.parse_grid('x?..')


def test_humanize_is_deterministic():
    from p5midi.flp import Note
    a = [Note(i * 24, 60, 24, 0) for i in range(16)]
    b = [Note(i * 24, 60, 24, 0) for i in range(16)]
    theory.humanize(a, 0.5, 96, seed=7, swing=0.5)
    theory.humanize(b, 0.5, 96, seed=7, swing=0.5)
    assert a == b and any(n.position % 24 for n in a)


SPEC = {
    'title': 'Test', 'bpm': 100, 'key': 'A minor', 'progression': ['i', 'VI', 'III', 'VII'],
    'parts': {
        'drums': {'type': 'drums', 'channel': 'Kick', 'lanes': {'kick': 'x...x...x...x...'}},
        'bass': {'type': 'bass', 'channel': 'Bass', 'rhythm': 'x-----.x--.x----'},
        'pad': {'type': 'chords', 'channel': 'Pad'},
        'arp': {'type': 'arp', 'channel': 'Pad', 'pattern': 'updown'},
        'lead': {'type': 'melody', 'channel': 'Bass', 'notes': [[0, 'E5', 1], [1, 5, 1, 90]]},
        'unused': {'type': 'notes', 'channel': 'Pad', 'notes': [[0, 60, 1]]},
    },
    'arrangement': [
        {'name': 'intro', 'bars': 4, 'parts': ['pad']},
        {'name': 'drop', 'bars': 8, 'parts': ['drums', 'bass', 'pad', 'arp', 'lead']},
    ],
}


def test_build_song(flp_file):
    res = build(SPEC, flp_file(ppq=96))
    from p5midi.flp import parse_events
    p = Project(*parse_events(res.project.to_bytes()))  # what FL will read
    assert res.total_bars == 12 and res.bpm == 100
    assert res.sections[1]['start_s'] == pytest.approx(9.6)
    assert res.parts['drums'].bars == 1 and len(res.parts['drums'].notes) == 4
    assert len(res.parts['pad'].notes) == 12 and res.parts['pad'].bars == 4
    assert res.parts['lead'].notes[1].key == theory.degree_to_note('A minor', 5, 5)
    clips = p.clips()
    drum_clips = [c for c in clips if c.pattern == res.parts['drums'].pattern_id]
    assert len(drum_clips) == 8 and drum_clips[0].position == 4 * 384
    assert any('unused' in w for w in res.warnings)
    names = [e[1].decode('utf-16-le').rstrip('\0') for e in p.events if e[0] == 239]
    assert names[:2] == ['pad', 'drums']
    d = {e[0]: e[1] for e in p.events}
    assert d[9] == b'\x01' and struct.unpack('<I', d[156])[0] == 100000


def test_build_errors_are_helpful(flp_file):
    bad = json.loads(json.dumps(SPEC))
    bad['parts']['drums']['channel'] = 'Violin'
    with pytest.raises(SpecError, match="Channels: 'Kick'"):
        build(bad, flp_file())
    bad = json.loads(json.dumps(SPEC))
    bad['arrangement'][0]['parts'] = ['nope']
    with pytest.raises(SpecError, match='unknown part'):
        build(bad, flp_file())


def test_midi_export(flp_file, tmp_path):
    res = build(SPEC, flp_file(ppq=96))
    out = export_midi(res, tmp_path / 'song.mid')
    data = out.read_bytes()
    assert data[:4] == b'MThd'
    fmt, ntracks, ppq = struct.unpack('>HHH', data[8:14])
    assert (fmt, ntracks, ppq) == (1, 1 + len(res.parts), 96)
    assert data.count(b'MTrk') == ntracks
    try:
        import mido
    except ImportError:
        return
    mf = mido.MidiFile(out)
    drums = next(t for t in mf.tracks if t.name == 'drums')
    ons = [m for m in drums if m.type == 'note_on' and m.velocity]
    assert len(ons) == 8 * 4 and all(m.channel == 9 for m in ons)
