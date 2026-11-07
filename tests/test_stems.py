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
