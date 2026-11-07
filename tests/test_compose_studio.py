import json
import threading
import urllib.error
import urllib.request

import pytest

from p5midi import compose as C


class FakeAI(C.AI):
    """Returns canned answers in order and records what it was sent."""
    def __init__(self, answers):
        super().__init__(key='x')
        self.answers, self.sent = list(answers), []

    def chat(self, messages):
        self.sent.append(messages)
        return self.answers.pop(0), {'prompt_tokens': 10, 'completion_tokens': 5}


def spec_for(tpl, channel='Kick'):
    return {'title': 'T', 'template': str(tpl), 'bpm': 120, 'key': 'C minor',
            'parts': {'k': {'type': 'drums', 'channel': channel, 'lanes': {'kick': 'x...x...x...x...'}}},
            'arrangement': [{'name': 'a', 'bars': 8, 'parts': ['k']}, {'name': 'b', 'bars': 8, 'parts': ['k']}]}


def test_parse_json_and_length():
    assert C.parse_json('```json\n{"a": 1}\n```') == {'a': 1}
    assert C.parse_json('Sure: {"a": 2} hope that helps') == {'a': 2}
    with pytest.raises(C.ComposeError):
        C.parse_json('  ')
    s = {'bpm': 120, 'arrangement': [{'name': 'a', 'bars': 8}, {'name': 'b', 'bars': 8}, {'name': 'c', 'bars': 8}]}
    C.enforce_length(s, 30)  # 30 s at 120 BPM = 15 bars
    assert [x['bars'] for x in s['arrangement']] == [8, 7]


def test_compose_retries_with_the_error_and_json_history(flp_file):
    tpl = flp_file()
    bad, good = spec_for(tpl, 'Violin'), spec_for(tpl)
    ai = FakeAI([json.dumps(bad), json.dumps(good)])
    spec, usage = C.compose('a beat', ai=ai, catalog={'Test': [{'channel': 'Kick', 'plugin': 'x'}]})
    assert spec['parts']['k']['channel'] == 'Kick'
    assert usage == {'prompt_tokens': 20, 'completion_tokens': 10}
    retry = ai.sent[1]
    assert retry[-2]['role'] == 'assistant' and json.loads(retry[-2]['content'])  # history stays JSON
    assert "no channel named 'Violin'" in json.loads(retry[-1]['content'])['error']
    assert 'Kick' in ai.sent[0][0]['content']  # catalog reached the system prompt


def test_compose_gives_up_after_three_bad_answers(flp_file):
    ai = FakeAI(['not json'] * 3)
    with pytest.raises(C.ComposeError, match='after 3 tries'):
        C.compose('x', ai=ai, catalog={'T': []})


def test_revise_sends_mix_notes_and_trims(flp_file):
    tpl = flp_file()
    ai = FakeAI([json.dumps(spec_for(tpl))])
    spec, _ = C.revise(spec_for(tpl), 'darker', ai=ai, max_seconds=20, catalog={'T': []},
                       analysis_notes=['Very bass-heavy'])
    ask = json.loads(ai.sent[0][1]['content'])
    assert ask['change'] == 'darker' and ask['mix_problems'] == ['Very bass-heavy']
    assert C.total_bars(spec) == 10  # 20 s at 120 BPM


def test_studio_lists_songs_and_guards_requests(tmp_path, monkeypatch):
    from p5midi import studio
    song = tmp_path / '20261107-120000-test-song'
    song.mkdir()
    (song / 'idea.txt').write_text('an idea', encoding='utf-8')
    (song / 'test-song.json').write_text(json.dumps({'title': 'Test Song'}), encoding='utf-8')
    (song / 'test-song.report.json').write_text(json.dumps(
        {'bpm': 90, 'parts': {'k': {}}, 'analysis': {'duration_s': 30, 'lufs': -10, 'notes': ['x'],
                                                     'sections': [{'name': 'a', 'lufs': -11}]}}), encoding='utf-8')
    (song / 'test-song.mp3').write_bytes(b'ID3')
    songs = studio.list_songs(tmp_path)
    assert songs[0]['title'] == 'Test Song' and songs[0]['versions'][0]['mp3'] is True

    monkeypatch.setattr(studio, 'config_path', lambda: tmp_path / 'cfg.json')
    studio.save_config({'out_dir': str(tmp_path)})
    srv = studio.ThreadingHTTPServer(('127.0.0.1', 0), studio.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{srv.server_address[1]}'

    def call(path, data=None, headers=None):
        req = urllib.request.Request(base + path, data=data, headers=headers or {}, method='POST' if data else 'GET')
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
    try:
        assert call('/api/state')[0] == 200
        assert call(f'/files/{song.name}/test-song.mp3') == (200, b'ID3')
        assert call('/files/..%2F..%2Fx/test-song.mp3')[0] == 404
        assert call('/api/settings', b'{}', {'content-type': 'application/json'})[0] == 403  # no x-p5 header
        assert call('/api/state', None, {'host': 'evil.example'})[0] == 403                # DNS rebinding
        code, body = call('/api/create', b'{"idea": ""}', {'content-type': 'application/json', 'x-p5': '1'})
        assert code == 400
    finally:
        srv.shutdown()
