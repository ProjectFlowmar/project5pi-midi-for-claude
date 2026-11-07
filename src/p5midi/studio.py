"""Project5pi MIDI Studio: a small local app. Type an idea, click Make, listen, open in FL Studio.

Run `p5midi studio`. It serves one page on 127.0.0.1 (never on the network) and opens
your browser. Settings live in %APPDATA%/p5midi/config.json (or ~/.config/p5midi).
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import __version__


def config_path() -> Path:
    base = os.environ.get('APPDATA') or os.path.join(Path.home(), '.config')
    return Path(base) / 'p5midi' / 'config.json'


def load_config() -> dict:
    try:
        cfg = json.loads(config_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        cfg = {}
    if not cfg.get('device'):  # anonymous id for the free tier; not tied to anything personal
        cfg['device'] = uuid.uuid4().hex
        try:
            save_config(cfg)
        except OSError:
            pass
    return cfg


def save_config(cfg: dict) -> None:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2), encoding='utf-8')


def out_dir(cfg: dict) -> Path:
    return Path(cfg.get('out_dir') or Path.home() / 'Music' / 'P5 MIDI Studio')


def own_key(cfg: dict) -> bool:
    return bool(cfg.get('ai_key') or os.environ.get('DEEPSEEK_API_KEY') or os.environ.get('P5MIDI_AI_KEY')
                or os.environ.get('OPENAI_API_KEY'))


def make_ai(cfg: dict):
    """Your own AI key if you set one; otherwise the hosted Project5pi service (free tier or license)."""
    from .compose import AI
    from .remote import Hosted
    if cfg.get('ai_key'):
        return AI(cfg.get('ai_url') or AI.url, cfg['ai_key'], cfg.get('ai_model') or AI.model)
    if own_key(cfg):
        return AI.from_env()
    return Hosted(cfg['device'], cfg.get('license', ''), cfg.get('server', ''))


_HOSTED = {'at': 0, 'data': None}


def hosted_info(cfg: dict, fresh: bool = False) -> dict:
    """Plans + this device's status from the service, cached 60 s. {'reachable': False} if it is down."""
    if not fresh and _HOSTED['data'] is not None and time.time() - _HOSTED['at'] < 60:
        return _HOSTED['data']
    from .remote import Hosted
    h = Hosted(cfg['device'], cfg.get('license', ''), cfg.get('server', ''))
    try:
        info = {'reachable': True, 'plans': h.plans(), 'status': h.status()}
    except Exception as e:
        info = {'reachable': False, 'error': str(e)[:200]}
    _HOSTED.update(at=time.time(), data=info)
    return info


class Jobs:
    def __init__(self):
        self.lock = threading.Lock()
        self.jobs: dict[str, dict] = {}
        self.busy = False

    def start(self, fn, *args) -> str:
        with self.lock:
            if self.busy:
                raise RuntimeError('A song is already being made. Wait for it to finish.')
            self.busy = True
            jid = uuid.uuid4().hex[:10]
            job = {'id': jid, 'status': 'running', 'steps': [], 'started': time.time()}
            self.jobs[jid] = job

        def step(s):
            job['steps'].append({'t': round(time.time() - job['started'], 1), 'text': s})

        def run():
            try:
                job['result'] = fn(*args, step=step)
                job['status'] = 'done'
            except Exception as e:  # shown to the user as one line
                job['status'] = 'error'
                job['error'] = str(e)[:500]
            finally:
                self.busy = False
        threading.Thread(target=run, daemon=True).start()
        return jid


JOBS = Jobs()
SAFE = re.compile(r'^[A-Za-z0-9._ -]+$')


def list_songs(root: Path) -> list[dict]:
    songs = []
    if not root.is_dir():
        return songs
    for d in sorted((p for p in root.iterdir() if p.is_dir()), reverse=True)[:60]:
        versions = []
        for rp in sorted(d.glob('*.report.json'), key=lambda p: p.stat().st_mtime):
            name = rp.name[:-len('.report.json')]
            try:
                rep = json.loads(rp.read_text(encoding='utf-8'))
            except ValueError:
                continue
            a = rep.get('analysis') or {}
            versions.append({'name': name, 'mp3': (d / f'{name}.mp3').exists(), 'bpm': rep.get('bpm'),
                             'stems': (d / f'{name}-stems').is_dir(),
                             'seconds': a.get('duration_s'), 'lufs': a.get('lufs'), 'notes': a.get('notes', []),
                             'sections': [[s['name'], s['lufs']] for s in a.get('sections', [])],
                             'parts': list((rep.get('parts') or {}).keys())})
        if not versions:
            continue
        idea = (d / 'idea.txt').read_text(encoding='utf-8') if (d / 'idea.txt').exists() else ''
        title = d.name.split('-', 2)[-1].replace('-', ' ').title()
        for sp in d.glob('*.json'):
            if not sp.name.endswith('.report.json'):
                try:
                    title = json.loads(sp.read_text(encoding='utf-8')).get('title') or title
                    break
                except ValueError:
                    pass
        songs.append({'id': d.name, 'title': title, 'idea': idea, 'versions': versions})
    return songs


class Handler(BaseHTTPRequestHandler):
    server_version = f'p5midi-studio/{__version__}'

    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    def _send(self, code, body, ctype='application/json'):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header('content-type', ctype)
        self.send_header('cache-control', 'no-store')
        self.send_header('content-length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _host_ok(self) -> bool:  # blocks DNS-rebinding pages from talking to the local app
        host = (self.headers.get('host') or '').split(':')[0]
        return host in ('127.0.0.1', 'localhost')

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {'error': 'host'})
        cfg = load_config()
        path = urlparse(self.path).path
        if path == '/':
            html = Path(__file__).with_name('studio.html').read_bytes()
            return self._send(200, html, 'text/html; charset=utf-8')
        if path == '/api/state':
            from .fl import FLNotFound, find_fl_exe, ffmpeg
            try:
                fl = str(find_fl_exe())
            except FLNotFound:
                fl = ''
            try:
                has_ffmpeg = bool(ffmpeg())
            except FileNotFoundError:
                has_ffmpeg = False
            mine = own_key(cfg)
            hosted = None if mine else hosted_info(cfg, fresh='fresh' in (urlparse(self.path).query or ''))
            has_ai = mine or bool(hosted and hosted.get('reachable'))
            return self._send(200, {'version': __version__, 'ai': has_ai, 'fl': fl,
                                    'mode': 'own' if mine else 'hosted', 'hosted': hosted,
                                    'license_set': bool(cfg.get('license')),
                                    'ffmpeg': has_ffmpeg, 'out_dir': str(out_dir(cfg)),
                                    'busy': JOBS.busy, 'songs': list_songs(out_dir(cfg))})
        if path.startswith('/api/job/'):
            job = JOBS.jobs.get(path.rsplit('/', 1)[-1])
            return self._send(200, job) if job else self._send(404, {'error': 'no job'})
        if path.startswith('/files/'):
            parts = [unquote(p) for p in path[len('/files/'):].split('/')]
            if len(parts) != 2 or not all(SAFE.match(p) and '..' not in p for p in parts) or \
                    not parts[1].endswith(('.mp3', '.wav')):
                return self._send(404, {'error': 'no'})
            f = out_dir(cfg) / parts[0] / parts[1]
            if not f.is_file():
                return self._send(404, {'error': 'no'})
            return self._send(200, f.read_bytes(), 'audio/mpeg' if f.suffix == '.mp3' else 'audio/wav')
        return self._send(404, {'error': 'not found'})

    def do_POST(self):
        if not self._host_ok() or self.headers.get('x-p5') != '1':  # custom header = no cross-site posts
            return self._send(403, {'error': 'forbidden'})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get('content-length') or 0)) or b'{}')
        except ValueError:
            return self._send(400, {'error': 'bad json'})
        cfg = load_config()
        path = urlparse(self.path).path
        try:
            if path == '/api/settings':
                if 'license' in body:
                    body['license'] = str(body['license']).strip().upper()
                    _HOSTED['data'] = None
                for k in ('ai_key', 'ai_url', 'ai_model', 'out_dir', 'license'):
                    if k in body:
                        v = str(body[k]).strip()
                        if v:
                            cfg[k] = v
                        else:
                            cfg.pop(k, None)
                save_config(cfg)
                return self._send(200, {'ok': True})
            if path == '/api/create':
                from .app import create
                idea = str(body.get('idea', '')).strip()
                if len(idea) < 3:
                    return self._send(400, {'error': 'Describe the song first.'})
                secs = float(body['seconds']) if body.get('seconds') else None
                ai = make_ai(cfg)
                def job(step):
                    try:
                        return create(idea, out_dir(cfg), ai=ai, max_seconds=secs, step=step)
                    finally:
                        _HOSTED['data'] = None  # songs-left changed
                jid = JOBS.start(job)
                return self._send(200, {'job': jid})
            if path == '/api/change':
                from .app import change
                song, instr = str(body.get('song', '')), str(body.get('instruction', '')).strip()
                if not SAFE.match(song) or not (out_dir(cfg) / song).is_dir() or not instr:
                    return self._send(400, {'error': 'Pick a song and say what to change.'})
                ai = make_ai(cfg)
                jid = JOBS.start(lambda step: change(out_dir(cfg) / song, instr, ai=ai, step=step))
                return self._send(200, {'job': jid})
            if path == '/api/stems':
                from .stems import export_stems
                song, name = str(body.get('song', '')), str(body.get('name', ''))
                if not SAFE.match(song) or not SAFE.match(name):
                    return self._send(400, {'error': 'bad name'})
                if not own_key(cfg) and (hosted_info(cfg).get('status') or {}).get('plan') != 'paid':
                    return self._send(402, {'error': 'Stems are part of Unlimited. Upgrade to export them.'})
                flp = out_dir(cfg) / song / f'{name}.flp'
                if not flp.is_file():
                    return self._send(404, {'error': 'missing project'})
                jid = JOBS.start(lambda step: {**export_stems(flp, step=step), 'title': f'stems for {name}'})
                return self._send(200, {'job': jid})
            if path == '/api/install-ffmpeg':
                from .fl import install_ffmpeg
                jid = JOBS.start(lambda step: (step('installing audio tools (ffmpeg), about a minute'),
                                               {'title': 'audio tools', 'seconds': 0, 'path': install_ffmpeg()})[1])
                return self._send(200, {'job': jid})
            if path == '/api/quit':
                self._send(200, {'ok': True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if path == '/api/portal':
                from .remote import Hosted
                url = Hosted(cfg['device'], cfg.get('license', ''), cfg.get('server', '')).portal()
                webbrowser.open(url)
                return self._send(200, {'ok': True})
            if path == '/api/open':
                song, name = str(body.get('song', '')), str(body.get('name', ''))
                if not SAFE.match(song) or (name and not SAFE.match(name)):
                    return self._send(400, {'error': 'bad name'})
                what = str(body.get('what', ''))
                target = out_dir(cfg) / song / ((f'{name}-stems' if what == 'stems' else f'{name}.flp') if name else '')
                if not target.exists():
                    return self._send(404, {'error': 'missing'})
                if sys.platform == 'win32':
                    os.startfile(str(target))  # .flp opens FL Studio; a folder opens Explorer
                else:
                    import subprocess
                    subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(target)])
                return self._send(200, {'ok': True})
        except Exception as e:
            return self._send(400, {'error': str(e)[:400]})
        return self._send(404, {'error': 'not found'})


def main(port: int = 47390, open_browser: bool = True) -> None:
    url = f'http://127.0.0.1:{port}/'
    try:
        srv = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    except OSError:  # already running: just show it
        if open_browser:
            webbrowser.open(url)
        return
    print(f'P5 MIDI Studio running at {url}  (Ctrl+C to stop)')
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
