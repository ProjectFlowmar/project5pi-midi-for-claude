"""Hosted composer for the Studio app: songs without your own AI key, free tier + paid licenses.

    python -m p5midi.hosted            (reads its settings from environment variables)

Endpoints (JSON):
    GET  /v1/plans                      prices, checkout links, founding seats left
    POST /v1/status   {device, license?}                      -> plan, songs left this month
    POST /v1/compose  {device, license?, idea, seconds, catalog} -> {spec, song, plan, left}
    POST /v1/revise   {device, license?, song, spec, instruction, notes?, catalog} -> {spec, ...}
    POST /v1/fix      {device, license?, song, spec, error, catalog} -> {spec}   (no quota: the user's build failed)
    POST /v1/portal   {license}         -> {url}  Stripe page to cancel or change the card
    POST /v1/resend   {email}           -> {ok}   emails the key again if that email bought one
    POST /stripe/webhook                Stripe events -> licenses (signature checked)
    GET  /buy/monthly | /buy/yearly     creates a Checkout Session with this product's own branding and redirects
                                        (Payment Links can only use the account-wide branding)

Free: 5 songs a month per device (15 per IP), up to 60 s, 2 changes per song.
Paid ($5/month or $39/year): up to 5 min, unlimited changes, 100 songs a day cap.
A global daily cap protects the AI bill. The service keeps emails only for license holders.

Environment: P5H_DB (sqlite path), P5H_PORT (8790), DEEPSEEK_API_KEY (or P5MIDI_AI_*),
STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, P5H_LINK_MONTHLY, P5H_LINK_YEARLY, P5H_PRICE_YEARLY,
BREVO_API_KEY, P5H_MAIL_FROM, P5H_DAILY_CAP (3000), P5H_SALT, P5H_PORTAL_CONFIG, P5H_RETURN_URL,
P5H_PRICE_MONTHLY, P5H_PRICE_YEARLY, P5H_BRAND_NAME, P5H_BRAND_ICON (Stripe file id), P5H_SUCCESS_URL.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__
from .compose import AI, ComposeError, catalog_check, compose, fix, revise

FREE_SONGS, FREE_SONGS_IP, FREE_SECONDS, FREE_CHANGES = 5, 15, 60, 2
PAID_SECONDS, PAID_DAILY = 300, 100
FOUNDING_SEATS = 100
ACTIVE = ('active', 'trialing', 'past_due')


def env(k, d=''):
    return os.environ.get(k, d)


# ---------------------------------------------------------------- storage
class DB:
    def __init__(self, path: str):
        self.c = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.lock = threading.Lock()
        self.c.executescript('''
          PRAGMA journal_mode=WAL;
          CREATE TABLE IF NOT EXISTS licenses(key TEXT PRIMARY KEY, email TEXT, customer TEXT, subscription TEXT UNIQUE,
            plan TEXT, status TEXT, period_end INTEGER, created INTEGER);
          CREATE TABLE IF NOT EXISTS usage(month TEXT, who TEXT, songs INTEGER, PRIMARY KEY(month, who));
          CREATE TABLE IF NOT EXISTS daily(day TEXT, who TEXT, n INTEGER, PRIMARY KEY(day, who));
          CREATE TABLE IF NOT EXISTS songs(id TEXT PRIMARY KEY, who TEXT, changes INTEGER, created INTEGER, autofix INTEGER DEFAULT 0);
          CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, at INTEGER);
          CREATE TABLE IF NOT EXISTS mails(email TEXT, at INTEGER);''')

    def q(self, sql, args=()):
        with self.lock:
            return self.c.execute(sql, args).fetchall()

    def bump(self, table, period, who, col):
        with self.lock:
            self.c.execute(f'INSERT INTO {table} VALUES(?,?,1) ON CONFLICT DO UPDATE SET {col}={col}+1', (period, who))

    def count(self, table, period, who, col):
        r = self.q(f'SELECT {col} FROM {table} WHERE {"month" if table == "usage" else "day"}=? AND who=?', (period, who))
        return r[0][0] if r else 0


def month() -> str:
    return time.strftime('%Y-%m', time.gmtime())


def today() -> str:
    return time.strftime('%Y-%m-%d', time.gmtime())


def h(s: str) -> str:  # store hashes of device ids and IPs, never the raw values
    return hashlib.sha256((env('P5H_SALT', 'p5midi') + s).encode()).hexdigest()[:24]


def new_key() -> str:
    a = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ'
    return 'P5M-' + '-'.join(''.join(secrets.choice(a) for _ in range(4)) for _ in range(4))


# ---------------------------------------------------------------- plans
def license_row(db: DB, key: str):
    if not key:
        return None
    r = db.q('SELECT key,email,customer,subscription,plan,status,period_end FROM licenses WHERE key=?', (key.strip().upper(),))
    if not r:
        return None
    row = dict(zip(('key', 'email', 'customer', 'subscription', 'plan', 'status', 'period_end'), r[0]))
    row['valid'] = row['status'] in ACTIVE and (row['period_end'] or 0) + 3 * 86400 > time.time()
    return row


def who_and_plan(db: DB, body: dict, ip: str) -> tuple[str, str, dict | None]:
    lic = license_row(db, str(body.get('license', '')))
    if lic and lic['valid']:
        return 'L:' + lic['key'], 'paid', lic
    dev = str(body.get('device', ''))[:64]
    if len(dev) < 8:
        raise ValueError('missing device id')
    return 'D:' + h(dev), 'free', lic


def songs_left(db: DB, who: str, plan: str, ip: str) -> int | None:
    if plan == 'paid':
        return max(0, PAID_DAILY - db.count('daily', today(), who, 'n'))
    used = db.count('usage', month(), who, 'songs')
    used_ip = db.count('usage', month(), 'I:' + h(ip), 'songs')
    return max(0, min(FREE_SONGS - used, FREE_SONGS_IP - used_ip))


def founding_left(db: DB) -> int:
    n = db.q("SELECT COUNT(*) FROM licenses WHERE plan='yearly'")[0][0]
    return max(0, FOUNDING_SEATS - n)


# ---------------------------------------------------------------- outside services
def stripe(method: str, path: str, data: dict | None = None) -> dict:
    req = urllib.request.Request('https://api.stripe.com/v1/' + path, method=method,
                                 data=urllib.parse.urlencode(data or {}, doseq=True).encode() if data else None,
                                 headers={'authorization': 'Bearer ' + env('STRIPE_SECRET_KEY')})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def stripe_signature_ok(payload: bytes, header: str, secret: str, tolerance: int = 300) -> bool:
    try:
        parts = dict(p.split('=', 1) for p in header.split(',') if '=' in p)
        t = int(parts['t'])
        sigs = [v for k, v in (p.split('=', 1) for p in header.split(',') if '=' in p) if k == 'v1']
    except (KeyError, ValueError):
        return False
    if abs(time.time() - t) > tolerance:
        return False
    want = hmac.new(secret.encode(), f'{t}.'.encode() + payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(want, s) for s in sigs)


def send_key_email(email: str, key: str, plan: str, mailer=None) -> None:
    text = (f"Thanks for joining P5 MIDI Studio ({'yearly' if plan == 'yearly' else 'monthly'} plan).\n\n"
            f"Your license key:\n\n    {key}\n\n"
            "Open the Studio, click Settings, and paste the key. That unlocks unlimited songs.\n\n"
            "To cancel or change your card any time, click Manage plan in the Studio's Settings.\n\n"
            "Questions: reply to this email.\n\nThe Project5pi team")
    payload = {'sender': {'name': 'Project5pi', 'email': env('P5H_MAIL_FROM', 'hello@project5pi.com')},
               'to': [{'email': email}], 'replyTo': {'email': env('P5H_MAIL_FROM', 'hello@project5pi.com')},
               'subject': 'Your P5 MIDI Studio license key', 'textContent': text}
    if mailer:
        return mailer(payload)
    req = urllib.request.Request('https://api.brevo.com/v3/smtp/email', method='POST', data=json.dumps(payload).encode(),
                                 headers={'api-key': env('BREVO_API_KEY'), 'content-type': 'application/json'})
    urllib.request.urlopen(req, timeout=30).read()


def handle_stripe_event(db: DB, ev: dict, mailer=None, stripe_call=stripe) -> str:
    """Apply one Stripe event. Returns what happened (for logs and tests)."""
    if db.q('SELECT 1 FROM events WHERE id=?', (ev.get('id', ''),)):
        return 'duplicate'
    db.q('INSERT INTO events VALUES(?,?)', (ev.get('id', ''), int(time.time())))
    typ, obj = ev.get('type', ''), (ev.get('data') or {}).get('object') or {}
    if typ == 'checkout.session.completed' and obj.get('mode') == 'subscription':
        sub_id = obj.get('subscription')
        if not sub_id or db.q('SELECT 1 FROM licenses WHERE subscription=?', (sub_id,)):
            return 'exists'
        sub = stripe_call('GET', f'subscriptions/{sub_id}')
        price = ((sub.get('items') or {}).get('data') or [{}])[0].get('price') or {}
        interval = (price.get('recurring') or {}).get('interval', 'month')
        email = ((obj.get('customer_details') or {}).get('email') or '').lower()
        key = new_key()
        db.q('INSERT INTO licenses VALUES(?,?,?,?,?,?,?,?)',
             (key, email, obj.get('customer'), sub_id, 'yearly' if interval == 'year' else 'monthly',
              sub.get('status', 'active'), int(sub.get('current_period_end') or
                                              ((sub.get('items') or {}).get('data') or [{}])[0].get('current_period_end') or
                                              time.time() + 32 * 86400), int(time.time())))
        if email:
            send_key_email(email, key, 'yearly' if interval == 'year' else 'monthly', mailer)
        return 'license created'
    if typ in ('customer.subscription.updated', 'customer.subscription.deleted', 'customer.subscription.created'):
        end = obj.get('current_period_end') or ((obj.get('items') or {}).get('data') or [{}])[0].get('current_period_end')
        status = 'canceled' if typ.endswith('deleted') else obj.get('status', '')
        db.q('UPDATE licenses SET status=?, period_end=COALESCE(?, period_end) WHERE subscription=?',
             (status, end, obj.get('id')))
        return f'subscription {status}'
    return 'ignored'


# ---------------------------------------------------------------- HTTP
def clean_catalog(raw) -> dict:
    if not isinstance(raw, dict) or not raw:
        raise ValueError('missing catalog')
    out = {}
    for name, chans in list(raw.items())[:20]:
        if isinstance(name, str) and isinstance(chans, list):
            out[name[:60]] = [{'channel': str(c.get('channel', ''))[:60], 'plugin': str(c.get('plugin', ''))[:60]}
                              for c in chans[:64] if isinstance(c, dict) and c.get('channel')]
    if not out:
        raise ValueError('empty catalog')
    return out


class Service:
    def __init__(self, db: DB, ai: AI | None = None, mailer=None, stripe_call=stripe):
        self.db, self.ai, self.mailer, self.stripe_call = db, ai, mailer, stripe_call

    def ai_(self) -> AI:
        return self.ai or AI.from_env()

    def global_ok(self) -> bool:
        return self.db.count('daily', today(), '*', 'n') < int(env('P5H_DAILY_CAP', '3000'))

    def plans(self) -> dict:
        return {'version': __version__, 'monthly': {'price': 5, 'url': env('P5H_LINK_MONTHLY')},
                'yearly': {'price': 39, 'url': env('P5H_LINK_YEARLY'), 'founding_left': founding_left(self.db)},
                'free': {'songs_per_month': FREE_SONGS, 'seconds': FREE_SECONDS, 'changes_per_song': FREE_CHANGES},
                'selling': bool(env('P5H_LINK_MONTHLY') and env('P5H_LINK_YEARLY'))}

    def status(self, body, ip) -> dict:
        who, plan, lic = who_and_plan(self.db, body, ip)
        out = {'plan': plan, 'left': songs_left(self.db, who, plan, ip),
               'max_seconds': PAID_SECONDS if plan == 'paid' else FREE_SECONDS}
        if lic:
            out['license'] = {'plan': lic['plan'], 'status': lic['status'], 'valid': lic['valid'],
                              'renews': lic['period_end']}
        return out

    def compose(self, body, ip) -> dict:
        who, plan, _ = who_and_plan(self.db, body, ip)
        if not self.global_ok():
            raise PermissionError('The service is very busy today. Please try again tomorrow.')
        if songs_left(self.db, who, plan, ip) <= 0:
            raise PermissionError('You have used your free songs this month. Upgrade for unlimited songs.'
                                  if plan == 'free' else 'Daily song limit reached. Try again tomorrow.')
        cat = clean_catalog(body.get('catalog'))
        cap = PAID_SECONDS if plan == 'paid' else FREE_SECONDS
        secs = min(float(body.get('seconds') or cap), cap)
        spec, usage = compose(str(body.get('idea', ''))[:2000], ai=self.ai_(), max_seconds=secs, catalog=cat,
                              validate=catalog_check(cat))
        song = secrets.token_hex(8)
        self.db.q('INSERT INTO songs VALUES(?,?,0,?,0)', (song, who, int(time.time())))
        self.db.bump('daily', today(), '*', 'n')
        if plan == 'paid':
            self.db.bump('daily', today(), who, 'n')
        else:
            self.db.bump('usage', month(), who, 'songs')
            self.db.bump('usage', month(), 'I:' + h(ip), 'songs')
        return {'spec': spec, 'song': song, 'plan': plan, 'left': songs_left(self.db, who, plan, ip), 'usage': usage}

    def _song(self, body, who, with_autofix=False):
        r = self.db.q('SELECT who, changes, autofix FROM songs WHERE id=?', (str(body.get('song', '')),))
        if not r or r[0][0] != who:
            raise ValueError('unknown song for this device or license')
        return (r[0][1], r[0][2]) if with_autofix else r[0][1]

    def revise(self, body, ip) -> dict:
        who, plan, _ = who_and_plan(self.db, body, ip)
        changes, autofix_used = self._song(body, who, with_autofix=True)
        autofix = bool(body.get('autofix')) and not autofix_used  # the one automatic mix fix is free
        if plan == 'free' and changes >= FREE_CHANGES and not autofix:
            raise PermissionError(f'Free songs include {FREE_CHANGES} changes. Upgrade for unlimited changes.')
        if not self.global_ok():
            raise PermissionError('The service is very busy today. Please try again tomorrow.')
        cat = clean_catalog(body.get('catalog'))
        cap = PAID_SECONDS if plan == 'paid' else FREE_SECONDS
        spec, usage = revise(body.get('spec') or {}, str(body.get('instruction', ''))[:1000], ai=self.ai_(),
                             max_seconds=cap, catalog=cat, analysis_notes=[str(n)[:300] for n in body.get('notes') or []][:8],
                             validate=catalog_check(cat))
        if autofix:
            self.db.q('UPDATE songs SET autofix=1 WHERE id=?', (body['song'],))
        else:
            self.db.q('UPDATE songs SET changes=changes+1 WHERE id=?', (body['song'],))
            changes += 1
        self.db.bump('daily', today(), '*', 'n')
        return {'spec': spec, 'song': body['song'], 'plan': plan, 'usage': usage,
                'changes_left': None if plan == 'paid' else max(0, FREE_CHANGES - changes)}

    def fix(self, body, ip) -> dict:
        who, plan, _ = who_and_plan(self.db, body, ip)
        self._song(body, who)
        cat = clean_catalog(body.get('catalog'))
        spec, usage = fix(body.get('spec') or {}, str(body.get('error', '')), ai=self.ai_(),
                          max_seconds=PAID_SECONDS if plan == 'paid' else FREE_SECONDS, catalog=cat,
                          validate=catalog_check(cat))
        return {'spec': spec, 'song': body['song'], 'usage': usage}

    def checkout_url(self, plan: str) -> str:
        price = env('P5H_PRICE_MONTHLY' if plan == 'monthly' else 'P5H_PRICE_YEARLY')
        if not price:
            raise ValueError('not selling yet')
        args = {'mode': 'subscription', 'line_items[0][price]': price, 'line_items[0][quantity]': 1,
                'allow_promotion_codes': 'true', 'billing_address_collection': 'auto',
                'success_url': env('P5H_SUCCESS_URL', 'https://project5pi.com/midi/thanks/'),
                'cancel_url': env('P5H_RETURN_URL', 'https://project5pi.com/midi/'),
                'metadata[app]': 'p5midi', 'subscription_data[metadata][app]': 'p5midi',
                'branding_settings[display_name]': env('P5H_BRAND_NAME', 'P5 MIDI Studio by Project5pi'),
                'branding_settings[background_color]': '#F1F2F0', 'branding_settings[button_color]': '#0D100F',
                'branding_settings[font_family]': 'inter', 'branding_settings[border_style]': 'rectangular'}
        if env('P5H_BRAND_ICON'):
            args.update({'branding_settings[icon][type]': 'file', 'branding_settings[icon][file]': env('P5H_BRAND_ICON')})
        return self.stripe_call('POST', 'checkout/sessions', args)['url']

    def portal(self, body, ip) -> dict:
        lic = license_row(self.db, str(body.get('license', '')))
        if not lic:
            raise ValueError('unknown license key')
        args = {'customer': lic['customer'], 'return_url': env('P5H_RETURN_URL', 'https://project5pi.com/midi/')}
        if env('P5H_PORTAL_CONFIG'):
            args['configuration'] = env('P5H_PORTAL_CONFIG')
        s = self.stripe_call('POST', 'billing_portal/sessions', args)
        return {'url': s['url']}

    def resend(self, body, ip) -> dict:
        email = str(body.get('email', '')).strip().lower()[:200]
        recent = self.db.q('SELECT COUNT(*) FROM mails WHERE email=? AND at>?', (email, int(time.time()) - 3600))[0][0]
        rows = self.db.q('SELECT key, plan FROM licenses WHERE email=? ORDER BY created DESC LIMIT 1', (email,))
        if rows and recent < 3:
            self.db.q('INSERT INTO mails VALUES(?,?)', (email, int(time.time())))
            send_key_email(email, rows[0][0], rows[0][1], self.mailer)
        return {'ok': True}  # same answer either way: does not reveal who bought


RATE = {}


def make_handler(svc: Service):
    class H(BaseHTTPRequestHandler):
        server_version = f'p5midi-hosted/{__version__}'

        def log_message(self, fmt, *args):
            pass

        def ip(self):
            return (self.headers.get('cf-connecting-ip') or self.client_address[0])[:64]

        def send(self, code, obj):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header('content-type', 'application/json')
            self.send_header('cache-control', 'no-store')
            self.send_header('content-length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path in ('/buy/monthly', '/buy/yearly'):
                bucket = RATE.setdefault('buy:' + self.ip(), [])
                now = time.time()
                bucket[:] = [t for t in bucket if now - t < 60]
                if len(bucket) >= 10:
                    return self.send(429, {'error': 'Too many requests. Wait a minute.'})
                bucket.append(now)
                try:
                    url = svc.checkout_url(self.path.rsplit('/', 1)[1])
                except Exception as e:
                    print('checkout error', type(e).__name__, str(e)[:200], flush=True)
                    return self.send(503, {'error': 'Checkout is not available right now. Please try again shortly.'})
                self.send_response(303)
                self.send_header('location', url)
                self.send_header('cache-control', 'no-store')
                self.send_header('content-length', '0')
                self.end_headers()
                return
            if self.path == '/v1/plans':
                return self.send(200, svc.plans())
            if self.path == '/health':
                return self.send(200, {'ok': True})
            return self.send(404, {'error': 'not found'})

        def do_POST(self):
            n = int(self.headers.get('content-length') or 0)
            if n > 400_000:
                return self.send(413, {'error': 'too big'})
            raw = self.rfile.read(n)
            if self.path == '/stripe/webhook':
                if not stripe_signature_ok(raw, self.headers.get('stripe-signature', ''), env('STRIPE_WEBHOOK_SECRET')):
                    return self.send(400, {'error': 'bad signature'})
                try:
                    what = handle_stripe_event(svc.db, json.loads(raw), svc.mailer, svc.stripe_call)
                    print('stripe', what, flush=True)
                    return self.send(200, {'ok': True})
                except Exception as e:
                    print('stripe error', e, flush=True)
                    return self.send(500, {'error': 'failed'})
            ip = self.ip()
            bucket = RATE.setdefault(ip, [])
            now = time.time()
            bucket[:] = [t for t in bucket if now - t < 60]
            if len(bucket) >= 12:
                return self.send(429, {'error': 'Too many requests. Wait a minute.'})
            bucket.append(now)
            try:
                body = json.loads(raw or b'{}')
                fn = {'/v1/status': svc.status, '/v1/compose': svc.compose, '/v1/revise': svc.revise,
                      '/v1/fix': svc.fix, '/v1/portal': svc.portal, '/v1/resend': svc.resend}.get(self.path)
                if not fn:
                    return self.send(404, {'error': 'not found'})
                return self.send(200, fn(body, ip))
            except PermissionError as e:
                return self.send(402, {'error': str(e)})
            except (ValueError, ComposeError) as e:
                return self.send(400, {'error': str(e)[:400]})
            except Exception as e:
                print('error', type(e).__name__, e, flush=True)
                return self.send(500, {'error': 'Something went wrong. Please try again.'})
    return H


def main() -> None:
    db = DB(env('P5H_DB', 'p5midi-hosted.db'))
    port = int(env('P5H_PORT', '8790'))
    srv = ThreadingHTTPServer(('127.0.0.1', port), make_handler(Service(db)))
    print(f'p5midi hosted {__version__} on 127.0.0.1:{port}', flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    main()
