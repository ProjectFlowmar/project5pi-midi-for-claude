import json

from p5midi import film, styles

STYLE = {'id': 'testpulse', 'name': 'Test Composer', 'kind': 'film', 'known_for': 'tests',
         'techniques': ['Repeating 16th-note string ostinato that grows by layers'],
         'params': {'bpm': [120, 120], 'progressions': ['i VI III VII'], 'ostinato': '16ths', 'pulse': False, 'drone': False,
                    'arp_rate': 0, 'layers': {'choir': 0.0, 'strings': 1.0, 'pluck': 0.2}, 'chord_every_seconds': 4,
                    'hits_per_minute': 2, 'dynamics': 'slow-build'}}


def test_style_loader_menu_and_brief(monkeypatch, tmp_path):
    (tmp_path / 'a.json').write_text(json.dumps([STYLE]), encoding='utf-8')
    monkeypatch.setattr(styles, 'DIR', tmp_path)
    assert [m['id'] for m in styles.menu('film')] == ['testpulse']
    text = styles.brief(styles.get('testpulse'))
    assert 'Test Composer' in text and 'ostinato' in text and 'never quote' in text
    assert styles.get(None) is None and styles.get('auto') is None


def test_style_shapes_the_score(monkeypatch, tmp_path):
    (tmp_path / 'a.json').write_text(json.dumps([STYLE]), encoding='utf-8')
    monkeypatch.setattr(styles, 'DIR', tmp_path)
    monkeypatch.setattr(film, 'SAMPLES', tmp_path)
    cues = {'key': 'D minor', 'cues': [{'start': 0, 'end': 40, 'mood': 'tension', 'intensity': 3}], 'hits': [], 'style': 'testpulse'}
    sheet = film.validate_cues(cues, [], [], 40.0)
    assert sheet['style'] == 'testpulse'
    spec = film.compose(sheet, [], [], 40.0)
    parts = spec['parts']
    assert 'sub' not in parts and 'choir' not in parts           # pulse off, choir weight 0
    strings = parts['strings']['notes']
    gaps = sorted({round(b[0] - a[0], 3) for a, b in zip(strings, strings[1:]) if b[0] > a[0]})
    assert gaps and gaps[0] <= 0.5 * film.BPM / 60 * 0.5          # 16th ostinato at the style tempo
    vels = [n[3] for n in strings]
    assert vels[-1] > vels[0]                                     # slow build
    pad_starts = sorted({n[0] for n in parts['pad']['notes']})
    assert len(pad_starts) >= 8                                    # a chord every ~4 s over 40 s
