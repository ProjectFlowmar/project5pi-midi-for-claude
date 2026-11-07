"""Client for the hosted composer (no AI key needed). The song is still built and rendered on
this machine; if a spec fails to build here, the error goes back to the service to fix (free)."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

from .compose import ComposeError, enforce_length, template_catalog
from .song import SpecError, build

DEFAULT_SERVER = 'https://midi-api.project5pi.com'


@dataclass
class Hosted:
    device: str
    license: str = ''
    server: str = ''
    timeout: int = 200

    def __post_init__(self):
        self.server = (self.server or os.environ.get('P5MIDI_SERVER') or DEFAULT_SERVER).rstrip('/')

    def _call(self, path: str, body: dict | None = None, timeout: int | None = None) -> dict:
        data = None if body is None else json.dumps({'device': self.device, 'license': self.license, **body}).encode()
        req = urllib.request.Request(self.server + path, data=data, method='POST' if data else 'GET',
                                     headers={'content-type': 'application/json', 'user-agent': 'p5midi-studio'})
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get('error')
            except ValueError:
                msg = None
            raise ComposeError(msg or f'service error {e.code}') from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ComposeError(f'could not reach the Project5pi service ({e})') from None

    def plans(self, timeout: int = 5) -> dict:
        return self._call('/v1/plans', timeout=timeout)

    def status(self, timeout: int = 5) -> dict:
        return self._call('/v1/status', {}, timeout=timeout)

    def portal(self) -> str:
        return self._call('/v1/portal', {})['url']

    def _checked(self, r: dict, catalog: dict, max_seconds) -> tuple[dict, dict]:
        spec, song, usage = r['spec'], r['song'], r.get('usage', {})
        for _ in range(2):
            try:
                build(spec)
                break
            except (SpecError, ValueError, KeyError, TypeError) as e:
                f = self._call('/v1/fix', {'song': song, 'spec': spec, 'error': str(e), 'catalog': catalog})
                spec = f['spec']
        else:
            build(spec)  # still broken: let the error surface
        spec['hosted_song'] = song
        return enforce_length(spec, max_seconds), usage

    def compose(self, idea: str, max_seconds: float | None = None) -> tuple[dict, dict]:
        cat = template_catalog()
        r = self._call('/v1/compose', {'idea': idea, 'seconds': max_seconds, 'catalog': cat})
        return self._checked(r, cat, max_seconds)

    def revise(self, spec: dict, instruction: str, max_seconds: float | None = None,
               analysis_notes: list[str] | None = None, autofix: bool = False) -> tuple[dict, dict]:
        song = spec.get('hosted_song')
        if not song:
            raise ComposeError('This song was made with your own AI key; change it in that mode.')
        cat = template_catalog([spec.get('template', 'Basic')])
        clean = {k: v for k, v in spec.items() if k != 'hosted_song'}
        r = self._call('/v1/revise', {'song': song, 'spec': clean, 'instruction': instruction,
                                      'notes': analysis_notes or [], 'catalog': cat,
                                      'autofix': autofix})
        return self._checked(r, cat, max_seconds)
