import json
from pathlib import Path

import pytest

from p5midi import app, packs, styles
from p5midi import compose as C
from p5midi import hosted as H
from test_compose_studio import FakeAI, spec_for
from test_hosted import body, svc  # noqa: F401  (fixture)

FL_TEMPLATES = {'Breakbeat', 'Chillout-Ambient', 'Chillout', 'Drum & Bass', 'Dubstep', 'EDM-House', 'Electro',
                'HipHop-Trap', 'Trap', 'Funk', 'Jazz', 'Metal', 'Rock', 'Basic'}


def test_packs_are_complete():
    m = packs.menu()
    assert len(m) >= 10 and {p['id'] for p in m if p['free']} == {'boom-bap', 'trap'}
    for p in packs.all_packs().values():
        assert p['template'] in FL_TEMPLATES and len(p['bpm']) == 2 and p['rules'] and p['example']
        if p.get('style'):
            assert styles.get(p['style'])
    text = packs.brief(packs.get('drill'))
    assert 'Genre pack: Drill' in text and 'triplets' in text and 'Style influence' in text
    with pytest.raises(KeyError):
        packs.get('polka')


def test_compose_sends_pack_and_take(flp_file):
    tpl = flp_file()
    ai = FakeAI([json.dumps(spec_for(tpl))])
    C.compose('a beat', ai=ai, catalog={'Test': [{'channel': 'Kick', 'plugin': 'x'}]}, pack='drill', variant=1)
    system, user = ai.sent[0][0]['content'], json.loads(ai.sent[0][1]['content'])
    assert 'Genre pack: Drill' in system and 'take 2' in user['take']


def test_create_makes_separate_takes(flp_file, tmp_path):
    tpl = flp_file()
    a, b = spec_for(tpl), spec_for(tpl)  # same title on purpose: folders must still differ
    ai = FakeAI([json.dumps(a), json.dumps(b)])
    steps = []
    r = app.create('a beat', tmp_path / 'songs', ai=ai, render=False, versions=2, pack='trap', step=steps.append)
    assert len(r['takes']) == 2 and r['takes'][0]['folder'] != r['takes'][1]['folder']
    assert r['takes'][1]['folder'].endswith('-take2')
    assert (Path(r['takes'][0]['folder']) / 'pack.txt').read_text() == 'trap'
    assert any(s.startswith('take 2 of 2') for s in steps)
    assert 'take' not in json.loads(ai.sent[0][1]['content']) and 'take' in json.loads(ai.sent[1][1]['content'])


def test_hosted_gates_packs_and_versions(svc):  # noqa: F811
    assert svc.compose(body(pack='trap'), '1.1.1.1')['plan'] == 'free'          # free pack works
    with pytest.raises(PermissionError, match='Unlimited'):
        svc.compose(body(pack='drill'), '1.1.1.1')
    with pytest.raises(PermissionError, match='versions'):
        svc.compose(body(variant=1), '1.1.1.1')
    with pytest.raises(KeyError):
        svc.compose(body(pack='polka'), '1.1.1.1')
    assert svc.plans()['max_versions'] == 3 and len(svc.plans()['packs']) >= 10
    ev = {'id': 'evt_9', 'type': 'checkout.session.completed', 'data': {'object': {
        'mode': 'subscription', 'subscription': 'sub_1', 'customer': 'cus_1', 'customer_details': {'email': 'a@b.co'}}}}
    H.handle_stripe_event(svc.db, ev, svc.mailer, svc.stripe_call)
    key = svc.db.q('SELECT key FROM licenses')[0][0]
    assert svc.compose(body(license=key, pack='drill', variant=2), '4.4.4.4')['plan'] == 'paid'
