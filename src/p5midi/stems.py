"""Stems: one WAV per part, all the same length and starting at 0, ready to drag into any DAW.

For each pattern in the song, a copy of the project keeps only that pattern's playlist
clips, FL Studio renders it, and every stem is padded to the longest one so they line up.
The template's mixer and master effects stay on, so each stem sounds like it does in the mix.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Callable

from .flp import Project


def _safe(name: str) -> str:
    return re.sub(r'[^A-Za-z0-9_-]+', '-', name).strip('-')[:40] or 'part'


def stem_projects(flp: str | Path, out_dir: str | Path) -> list[tuple[str, Path]]:
    """Write one .flp per pattern that is used in the playlist. Returns [(part name, path)]."""
    flp, out_dir = Path(flp), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = Project.load(flp)
    clips = base.clips()
    names = base.pattern_names()
    used = sorted({c.pattern for c in clips})
    made = []
    for pid in used:
        p = Project.load(flp)
        p.set_clips([c for c in clips if c.pattern == pid])
        p.set_song_mode(True)
        name = names.get(pid) or f'pattern-{pid}'
        path = out_dir / f'{flp.stem}-stem-{_safe(name)}.flp'
        p.save(path)
        made.append((name, path))
    return made


def export_stems(flp: str | Path, out_dir: str | Path | None = None, step: Callable[[str], None] = lambda s: None) -> dict:
    """Render every part on its own. Returns {'folder', 'stems': [{part, file, seconds}]}."""
    from .analyze import _run, duration
    from .fl import render
    flp = Path(flp)
    out_dir = Path(out_dir) if out_dir else flp.with_name(f'{flp.stem}-stems')
    work = out_dir / '_projects'
    projects = stem_projects(flp, work)
    rendered = []
    for i, (name, path) in enumerate(projects, 1):
        step(f'rendering stem {i} of {len(projects)}: {name}')
        rendered.append((name, render(path, 'wav')))
    longest = max(duration(w) for _, w in rendered) if rendered else 0
    step('lining up the stems')
    stems = []
    for name, wav in rendered:
        dst = out_dir / f'{flp.stem} - {_safe(name)}.wav'
        _run(['-y', '-i', str(wav), '-af', f'apad=whole_dur={longest:.3f}', '-c:a', 'pcm_s24le', str(dst)])
        stems.append({'part': name, 'file': str(dst), 'seconds': duration(dst)})
    shutil.rmtree(work, ignore_errors=True)
    return {'folder': str(out_dir.resolve()), 'stems': stems, 'seconds': longest}
