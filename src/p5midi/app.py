"""The idea -> finished song flow used by `p5midi create` and the Studio app.

compose (AI) -> build + render + master + analyze -> if the mix check finds problems,
revise once with those notes and make it again. Each song gets its own folder with
the spec, project, MIDI, WAV, MP3 and a report.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Callable

from .compose import AI, compose, revise

Step = Callable[[str], None]


def _slug(s: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')[:40] or 'song'


def _make(spec: dict, folder: Path, name: str, step: Step, render: bool) -> dict:
    from .cli import make
    spec_path = folder / f'{name}.json'
    spec_path.write_text(json.dumps(spec, indent=2), encoding='utf-8')
    step('building the FL Studio project' if not render else 'building, rendering in FL Studio, mastering')
    rep = make(str(spec_path), str(folder / f'{name}.flp'), skip_render=not render)
    (folder / f'{name}.report.json').write_text(json.dumps(rep, indent=2), encoding='utf-8')
    return rep


def _notes(rep: dict) -> list[str]:
    return list((rep.get('analysis') or {}).get('notes') or [])


def create(idea: str, out_dir: str | Path = 'songs', ai: AI | None = None, max_seconds: float | None = None,
           fix: bool = True, render: bool = True, step: Step = lambda s: None) -> dict:
    t0 = time.time()
    step('writing the song')
    spec, usage = compose(idea, ai=ai, max_seconds=max_seconds)
    folder = Path(out_dir) / f"{time.strftime('%Y%m%d-%H%M%S')}-{_slug(spec.get('title') or idea)}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'idea.txt').write_text(idea, encoding='utf-8')
    name = _slug(spec.get('title') or 'song')
    rep = _make(spec, folder, name, step, render)
    history = [{'step': 'compose', 'notes': _notes(rep)}]
    if fix and render and _notes(rep):
        step('fixing the mix: ' + '; '.join(n.split('.')[0] for n in _notes(rep))[:160])
        spec, u2 = revise(spec, 'Fix the mix problems listed.', ai=ai, max_seconds=max_seconds,
                          analysis_notes=_notes(rep))
        usage = {k: usage.get(k, 0) + u2.get(k, 0) for k in usage}
        rep = _make(spec, folder, name, step, render)
        history.append({'step': 'auto-fix', 'notes': _notes(rep)})
    step('done')
    return {'folder': str(folder.resolve()), 'name': name, 'title': spec.get('title', name), 'report': rep,
            'history': history, 'usage': usage, 'seconds': round(time.time() - t0, 1)}


def change(folder: str | Path, instruction: str, ai: AI | None = None, max_seconds: float | None = None,
           render: bool = True, step: Step = lambda s: None) -> dict:
    """Revise the latest version of a song in its folder: 'more energy', 'darker', ..."""
    t0 = time.time()
    folder = Path(folder)
    specs = sorted(p for p in folder.glob('*.json') if not p.name.endswith('.report.json'))
    if not specs:
        raise FileNotFoundError(f'no song spec in {folder}')
    base = max(specs, key=lambda p: p.stat().st_mtime)
    spec = json.loads(base.read_text(encoding='utf-8'))
    step(f'changing: {instruction[:80]}')
    spec, usage = revise(spec, instruction, ai=ai, max_seconds=max_seconds)
    stem = re.sub(r'-v\d+$', '', base.stem)
    n = 2
    while (folder / f'{stem}-v{n}.json').exists():
        n += 1
    name = f'{stem}-v{n}'
    rep = _make(spec, folder, name, step, render)
    step('done')
    return {'folder': str(folder.resolve()), 'name': name, 'title': spec.get('title', name), 'report': rep,
            'usage': usage, 'seconds': round(time.time() - t0, 1)}
