import json

from p5midi import film


SRT = """1
00:00:00,000 --> 00:00:02,000
Three Mile Island.

2
00:00:02,100 --> 00:00:04,000
In 1979 that name became

3
00:00:04,100 --> 00:00:06,000
shorthand for fear.

4
00:00:20,000 --> 00:00:24,000
So how did it happen?
"""

REGIONS = [{'start': 8.0, 'end': 14.0, 'kind': 'intro'}, {'start': 14.0, 'end': 16.0, 'kind': 'title'},
           {'start': 30.0, 'end': 40.0, 'kind': 'sponsor'}, {'start': 50.0, 'end': 60.0, 'kind': 'end_card'}]


def test_read_and_merge_srt(tmp_path):
    p = tmp_path / 'c.srt'
    p.write_text(SRT, encoding='utf-8')
    lines = film.read_srt(p)
    assert len(lines) == 4 and lines[1]['start'] == 2.1
    merged = film.merge_lines(lines)
    assert [m['text'] for m in merged][:2] == ['Three Mile Island.', 'In 1979 that name became shorthand for fear.']


def test_validate_cues_fills_gaps_forces_sponsor_and_filters_hits():
    sheet = {'key': 'E minor', 'cues': [
        {'start': 0, 'end': 20, 'mood': 'unease', 'intensity': 9},
        {'start': 18, 'end': 45, 'mood': 'banana', 'intensity': 2},      # overlap + unknown mood
        {'start': 45, 'end': 46.5, 'mood': 'wonder', 'intensity': 2}],   # too short -> folded
        'hits': [{'t': 10, 'kind': 'boom'}, {'t': 21, 'kind': 'crack'}, {'t': 25, 'kind': 'zap'}, {'t': 59.5, 'kind': 'boom'}]}
    v = film.validate_cues(sheet, [], REGIONS, 60.0)
    assert v['key'] == 'E minor'
    assert v['cues'][0]['intensity'] == 5
    spans = [(c['start'], c['end']) for c in v['cues']]
    assert all(a < b for a, b in spans) and all(spans[i][1] <= spans[i + 1][0] for i in range(len(spans) - 1))
    sponsor = [c for c in v['cues'] if c['start'] == 30.0]
    assert sponsor and sponsor[0]['mood'] == 'warmth' and sponsor[0]['end'] == 40.0
    assert v['cues'][-1]['end'] == 60.0
    assert [h['t'] for h in v['hits']] == [21.0]   # 10 is in the intro, 25 too close, 59.5 too near the end


def test_compose_keeps_intro_silent_and_marks_cards(monkeypatch, tmp_path):
    monkeypatch.setattr(film, 'SAMPLES', tmp_path)       # no sample library: template sounds only
    sheet = film.validate_cues({'key': 'D minor', 'cues': [{'start': 0, 'end': 50, 'mood': 'tension', 'intensity': 3}],
                                'hits': [{'t': 20, 'kind': 'swell'}]}, [], REGIONS, 60.0)
    spec = film.compose(sheet, [{'start': 0, 'end': 6, 'text': 'x'}, {'start': 20, 'end': 24, 'text': 'y'}], REGIONS, 60.0)
    beats = film.BPM / 60
    for part in spec['parts'].values():
        for b, _p, length, _v in part['notes']:
            start, end = b / beats, (b + length) / beats
            assert not (8.0 < start < 14.0), (part['channel'], start)   # nothing starts inside the intro
    strings = [n[0] / beats for n in spec['parts']['strings']['notes']]
    assert any(abs(t - 14.0) < 0.01 for t in strings) and any(abs(t - 50.0) < 0.01 for t in strings)
    assert spec['template'] == 'Chillout-Ambient' and all(k.get('from') for k in spec['kit']['channels'])


def test_regions_from_docv2_scenes():
    scenes = [{'start': 0, 'end': 7, 'type': 'photo'}, {'start': 41.3, 'end': 54.8, 'type': 'intro'},
              {'start': 59.8, 'end': 88.7, 'type': 'lc'}, {'start': 88.7, 'end': 91.2, 'type': 'act'}]
    assert [r['kind'] for r in film.regions_from_scenes(scenes)] == ['intro', 'sponsor', 'act']


def test_plan_falls_back_to_heuristic_without_ai(monkeypatch):
    for k in ('DEEPSEEK_API_KEY', 'P5MIDI_AI_KEY', 'P5MIDI_AI_URL', 'OPENAI_API_KEY'):
        monkeypatch.delenv(k, raising=False)
    lines = [{'start': 0, 'end': 5, 'text': 'The disaster began.'}, {'start': 6, 'end': 12, 'text': 'Why did it happen?'}]
    sheet = film.plan_cues(lines, [], 14.0)
    assert sheet['source'].startswith('heuristic')
    assert {c['mood'] for c in sheet['cues']} >= {'tension', 'question'}


def test_short_videos_get_free_license_music(monkeypatch, tmp_path):
    from p5midi import library
    calls = {}
    monkeypatch.setattr(library, 'pick_track', lambda brief, d: {'id': 7, 'name': 'Sunny', 'artist': 'A', 'genre': 'Pop',
                                                                 'seconds': 90, 'why': 'fits', 'url': 'x'})
    monkeypatch.setattr(library, 'fetch', lambda t, used_for='': calls.setdefault('fetched', tmp_path / 't.mp3'))
    monkeypatch.setattr(library, 'fit', lambda mp3, d, out: calls.setdefault('fit', (d, out)) and out)
    monkeypatch.setattr(film, 'score_film', lambda *a, **k: calls.setdefault('scored', True) and {'wav': 'w', 'cue_sheet': {}})
    r = film.soundtrack(60.0, tmp_path / 'a', title='Promo')
    assert r['mode'] == 'free-license' and r['track']['name'] == 'Sunny' and 'Mixkit' in r['license']
    assert calls['fit'][0] == 60.0 and 'scored' not in calls
    r = film.soundtrack(240.0, tmp_path / 'b', title='Doc')
    assert r['mode'] == 'fl-studio-score' and calls.get('scored')
    calls.pop('scored')
    assert film.soundtrack(60.0, tmp_path / 'c', force_score=True)['mode'] == 'fl-studio-score'


def test_parse_mixkit_jsonld():
    from p5midi.library import parse_tag_page
    html = ('<script type="application/ld+json">{"@graph":[{"@type":"ItemList","itemListElement":[{"@type":"MusicRecording",'
            '"name":"Silent Descent","genre":"Film Score","byArtist":"E M","duration":"PT2M40S",'
            '"url":"https://assets.mixkit.co/music/614/614.mp3","license":"https://mixkit.co/license/#musicFree"}]}]}</script>')
    t = parse_tag_page(html, 'cinematic')
    assert t == [{'id': 614, 'name': 'Silent Descent', 'artist': 'E M', 'genre': 'Film Score', 'seconds': 160.0,
                  'url': 'https://assets.mixkit.co/music/614/614.mp3', 'tags': ['cinematic'],
                  'license': 'https://mixkit.co/license/#musicFree'}]
