from p5midi.flp import Clip, Note, Project
from p5midi.stems import stem_projects


def test_one_project_per_used_pattern_with_only_its_clips(flp_file, tmp_path):
    p = Project.load(flp_file())
    a = p.add_pattern([Note(0, 36, 24, 0)], name='drums')
    b = p.add_pattern([Note(0, 48, 96, 1)], name='808 bass')
    p.add_pattern([Note(0, 60, 24, 2)], name='unused')
    p.set_clips([Clip(a, 1, 0, 384), Clip(a, 1, 384, 384), Clip(b, 2, 384, 384)])
    src = tmp_path / 'song.flp'
    p.save(src)
    made = stem_projects(src, tmp_path / 'stems')
    assert [n for n, _ in made] == ['drums', '808 bass']
    assert made[1][1].name == 'song-stem-808-bass.flp'
    drums = Project.load(made[0][1]).clips()
    assert {c.pattern for c in drums} == {a} and len(drums) == 2
    bass = Project.load(made[1][1]).clips()
    assert [(c.pattern, c.position) for c in bass] == [(b, 384)]  # keeps its place in time


def test_kit_adds_sampler_and_copied_channels(flp_file, tmp_path):
    import struct
    from p5midi.flp import parse_events, serialize_events
    # a donor with a plain Sampler channel (empty plugin name + sample path)
    h, ev = parse_events(flp_file(channels=('Kick',)).read_bytes())
    i = next(n for n, e in enumerate(ev) if e[0] == 201)
    ev[i][1] = b'\x00\x00'
    ev.insert(i + 1, [196, 'C:\old.wav\0'.encode('utf-16-le')])
    ev.insert(i + 1, [143, struct.pack('<i', 7)])
    donor = Project(h, ev)
    wav = tmp_path / 'snare.wav'
    wav.write_bytes(b'RIFF')
    p = Project.load(flp_file(channels=('Bass', 'Pad')))
    sid = p.add_sampler('My Snare', wav, donor=donor)
    pid = p.copy_channel_from(Project.load(flp_file(channels=('Piano',))), 'Piano', name='Keys')
    q = Project(*parse_events(p.to_bytes()))
    labels = {c.id: c for c in q.channels()}
    assert labels[sid].label == 'My Snare' and labels[sid].sample.endswith('snare.wav')
    assert labels[pid].label == 'Keys'
    assert struct.unpack('<H', q.header[2:4])[0] == 4          # header channel count follows
    blk = dict((cid, b) for cid, b in q.channel_blocks())[sid]
    assert [struct.unpack('<i', e[1])[0] for e in blk if e[0] == 143] == [0]  # routed to master
