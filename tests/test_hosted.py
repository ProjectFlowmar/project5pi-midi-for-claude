import hashlib
import hmac
import json
import time

import pytest

from p5midi import hosted as H
from p5midi.compose import AI

CAT = {'Trap': [{'channel': 'Drums', 'plugin': 'FPC'}, {'channel': 'Sub 1', 'plugin': '3x Osc'}]}
SPEC = {'title': 'X', 'template': 'Trap', 'bpm': 120, 'key': 'A minor',
        'parts': {'d': {'type': 'drums', 'channel': 'Drums', 'lanes': {'kick': 'x...'}}},
        'arrangement': [{'name': 'a', 'bars': 64, 'parts': ['d']}]}


class FakeAI(AI):
    def __init__(self):
        super().__init__(key='x')
        self.calls = 0

    def chat(self, messages):
        self.calls += 1
        return json.dumps(SPEC), {'prompt_tokens': 1, 'completion_tokens': 1}


@pytest.fixture
def svc(tmp_path):
    mails = []
    svc_calls = []
    subs = {'sub_1': {'status': 'active', 'current_period_end': int(time.time()) + 30 * 86400,
                      'items': {'data': [{'price': {'recurring': {'interval': 'year'}}}]}}}

    def stripe_call(method, path, data=None):
        if path.startswith('subscriptions/'):
            return subs[path.split('/')[1]]
        if path == 'checkout/sessions':
            svc_calls.append(data)
            return {'url': 'https://checkout.stripe.com/c/pay/cs_test_' + data['line_items[0][price]']}
        if path == 'billing_portal/sessions':
            return {'url': 'https://billing.stripe.com/p/session/test_' + data['customer']}
        raise AssertionError(path)
    s = H.Service(H.DB(str(tmp_path / 'h.db')), ai=FakeAI(), mailer=mails.append, stripe_call=stripe_call)
    s.mails = mails
    s.checkout_calls = svc_calls
    return s


def body(**kw):
    return {'device': 'device-123456', 'catalog': CAT, 'idea': 'a beat', **kw}


def test_free_tier_limits_and_length(svc):
    r = svc.compose(body(seconds=200), '1.1.1.1')
    assert r['plan'] == 'free' and r['left'] == 4
    assert sum(s['bars'] for s in r['spec']['arrangement']) == 30  # 60 s at 120 BPM, not 64 bars
    for _ in range(4):
        svc.compose(body(), '1.1.1.1')
    with pytest.raises(PermissionError, match='free songs'):
        svc.compose(body(), '1.1.1.1')
    # a new device on the same IP still counts against the IP cap (15)
    assert svc.status(body(device='other-device-1'), '1.1.1.1')['left'] == 5
    with pytest.raises(ValueError):
        svc.compose(body(device='x'), '1.1.1.1')


def test_free_changes_are_capped_and_tied_to_the_device(svc):
    song = svc.compose(body(), '2.2.2.2')['song']
    rv = lambda **kw: svc.revise(body(song=song, spec=SPEC, instruction='darker', **kw), '2.2.2.2')
    assert rv(autofix=True)['changes_left'] == 2   # the automatic mix fix is free, once
    assert rv()['changes_left'] == 1
    assert rv()['changes_left'] == 0
    with pytest.raises(PermissionError):
        rv()
    with pytest.raises(ValueError, match='unknown song'):
        svc.revise(body(device='someone-else-9', song=song, spec=SPEC, instruction='x'), '2.2.2.2')
    assert svc.fix(body(song=song, spec=SPEC, error='bad grid'), '2.2.2.2')['spec']['template'] == 'Trap'


def test_catalog_check_rejects_unknown_channels(svc):
    bad = dict(SPEC, parts={'d': {'type': 'drums', 'channel': 'Violin', 'lanes': {}}})
    svc.ai.chat = lambda m: (json.dumps(bad), {})
    with pytest.raises(Exception, match='after 3 tries'):
        svc.compose(body(), '3.3.3.3')


def sign(payload: bytes, secret='whsec_test', t=None):
    t = t or int(time.time())
    return f't={t},v1=' + hmac.new(secret.encode(), f'{t}.'.encode() + payload, hashlib.sha256).hexdigest()


def test_stripe_signature():
    p = b'{"id":"evt_1"}'
    assert H.stripe_signature_ok(p, sign(p), 'whsec_test')
    assert not H.stripe_signature_ok(p, sign(p, 'other'), 'whsec_test')
    assert not H.stripe_signature_ok(p, sign(p, t=int(time.time()) - 3600), 'whsec_test')
    assert not H.stripe_signature_ok(p, 'garbage', 'whsec_test')


def test_purchase_creates_emailed_license_that_unlocks(svc):
    ev = {'id': 'evt_1', 'type': 'checkout.session.completed', 'data': {'object': {
        'mode': 'subscription', 'subscription': 'sub_1', 'customer': 'cus_1',
        'customer_details': {'email': 'Fan@Example.com'}}}}
    assert H.handle_stripe_event(svc.db, ev, svc.mailer, svc.stripe_call) == 'license created'
    assert H.handle_stripe_event(svc.db, ev, svc.mailer, svc.stripe_call) == 'duplicate'
    assert len(svc.mails) == 1 and svc.mails[0]['to'] == [{'email': 'fan@example.com'}]
    key = svc.db.q('SELECT key FROM licenses')[0][0]
    assert key in svc.mails[0]['textContent'] and key.startswith('P5M-')
    assert svc.plans()['yearly']['founding_left'] == 99

    st = svc.status(body(license=key.lower()), '4.4.4.4')
    assert st['plan'] == 'paid' and st['max_seconds'] == 300
    r = svc.compose(body(license=key, seconds=200), '4.4.4.4')
    assert r['plan'] == 'paid' and sum(s['bars'] for s in r['spec']['arrangement']) == 64
    assert svc.portal({'license': key}, '')['url'].endswith('cus_1')

    cancel = {'id': 'evt_2', 'type': 'customer.subscription.deleted', 'data': {'object': {'id': 'sub_1', 'status': 'canceled'}}}
    H.handle_stripe_event(svc.db, cancel, svc.mailer, svc.stripe_call)
    assert svc.status(body(license=key), '4.4.4.4')['plan'] == 'free'


def test_resend_does_not_reveal_buyers(svc):
    assert svc.resend({'email': 'nobody@example.com'}, '') == {'ok': True}
    assert svc.mails == []


def test_global_daily_cap(svc, monkeypatch):
    monkeypatch.setenv('P5H_DAILY_CAP', '1')
    svc.compose(body(), '5.5.5.5')
    with pytest.raises(PermissionError, match='busy'):
        svc.compose(body(device='device-abcdef'), '6.6.6.6')


def test_client_talks_to_service_and_fixes_local_build_errors(svc, monkeypatch):
    import threading
    from p5midi import remote
    from p5midi.song import SpecError
    srv = H.ThreadingHTTPServer(('127.0.0.1', 0), H.make_handler(svc))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    H.RATE.clear()
    try:
        monkeypatch.setattr(remote, 'template_catalog', lambda names=None: CAT)
        fails = {'n': 1}

        def fake_build(spec):
            if fails['n']:
                fails['n'] -= 1
                raise SpecError('bad grid in part d')
        monkeypatch.setattr(remote, 'build', fake_build)
        c = remote.Hosted('device-client-1', server=f'http://127.0.0.1:{srv.server_address[1]}')
        assert c.plans()['free']['songs_per_month'] == 5
        spec, _ = c.compose('a beat', 60)
        assert spec['hosted_song'] and svc.ai.calls == 2        # compose + one /v1/fix
        assert c.status()['left'] == 4
        spec2, _ = c.revise(spec, 'darker', 60)
        assert spec2['hosted_song'] == spec['hosted_song']
        with pytest.raises(remote.ComposeError, match='own AI key'):
            c.revise({'template': 'Trap'}, 'x')
    finally:
        srv.shutdown()


def test_branded_checkout_session(svc, monkeypatch):
    with pytest.raises(ValueError, match='not selling'):
        svc.checkout_url('monthly')
    monkeypatch.setenv('P5H_PRICE_MONTHLY', 'price_m')
    monkeypatch.setenv('P5H_BRAND_ICON', 'file_123')
    assert svc.checkout_url('monthly').endswith('price_m')
    a = svc.checkout_calls[-1]
    assert a['mode'] == 'subscription' and a['allow_promotion_codes'] == 'true'
    assert a['branding_settings[display_name]'] == 'P5 MIDI Studio by Project5pi'
    assert a['branding_settings[icon][file]'] == 'file_123'


def test_buy_link_get_makes_no_stripe_session(svc, monkeypatch):
    # 11/8: crawlers opening /buy/* minted live sessions that later alerted as "checkout abandoned"
    import threading, urllib.request, urllib.error
    monkeypatch.setenv('P5H_PRICE_MONTHLY', 'price_m')
    srv = H.ThreadingHTTPServer(('127.0.0.1', 0), H.make_handler(svc))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    H.RATE.clear()
    base = f'http://127.0.0.1:{srv.server_address[1]}'
    try:
        before = len(svc.checkout_calls)
        html = urllib.request.urlopen(base + '/buy/monthly').read().decode()
        assert 'method="post" action="/buy/monthly"' in html and len(svc.checkout_calls) == before

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        try:
            urllib.request.build_opener(NoRedirect).open(urllib.request.Request(base + '/buy/monthly', data=b'', method='POST'))
            raise AssertionError('expected a 303')
        except urllib.error.HTTPError as e:
            assert e.code == 303 and e.headers['location'].endswith('price_m')
        assert len(svc.checkout_calls) == before + 1
    finally:
        srv.shutdown()
