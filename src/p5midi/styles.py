"""Influence database: signature techniques of successful composers and producers (researched, sourced),
as text for the AI and as parameters for the composer. Styles only, never their melodies.

Files: p5midi/styles/*.json (each a list of entries, or one entry). Entry fields: id, name, kind
(film|beat|shortform), known_for, techniques[], params{bpm, mode, harmony, progressions[], ostinato,
pulse, drone, arp_rate, layers{}, chord_every_seconds, hits_per_minute, dynamics, drums}, sources[].
"""
from __future__ import annotations

import json
from pathlib import Path

DIR = Path(__file__).with_name('styles')


def all_styles() -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not DIR.is_dir():
        return out
    for f in sorted(DIR.glob('*.json')):
        try:
            data = json.loads(f.read_text(encoding='utf-8'))
        except ValueError:
            continue
        for e in data if isinstance(data, list) else [data]:
            if isinstance(e, dict) and e.get('id'):
                out[str(e['id'])] = e
    return out


def get(style: str | None) -> dict | None:
    if not style or style in ('none', 'auto'):
        return None
    s = all_styles()
    if style not in s:
        raise KeyError(f'unknown style {style!r}. Styles: {", ".join(sorted(s))}')
    return s[style]


def menu(kind: str | None = None) -> list[dict]:
    """Short list for an AI to choose from."""
    return [{'id': k, 'name': v.get('name'), 'known_for': v.get('known_for', '')[:160]}
            for k, v in sorted(all_styles().items()) if kind is None or v.get('kind') == kind]


def brief(style: dict | None) -> str:
    """Text block an AI prompt can include."""
    if not style:
        return ''
    p = style.get('params') or {}
    lines = [f"Style influence: {style.get('name')} ({style.get('known_for', '')}). Use these TECHNIQUES; write original material,",
             'never quote their melodies:']
    lines += [f'- {t}' for t in style.get('techniques', [])[:8]]
    if p.get('harmony'):
        lines.append(f"- Harmony: {p['harmony']}")
    if p.get('drums') and p['drums'] != 'none':
        lines.append(f"- Drums: {p['drums']}")
    if p.get('bpm'):
        lines.append(f"- Tempo range: {p['bpm'][0]}-{p['bpm'][1]} BPM")
    return '\n'.join(lines)
