"""Genre packs: ready-made genre recipes (template, tempo range, production rules, optional producer style)
the AI follows when it writes a song. Files: p5midi/packs/*.json, each a list of packs.

In the hosted app, packs marked "free": false and extra versions per idea are part of Unlimited.
With your own AI key (or the open-source CLI) every pack is available.
"""
from __future__ import annotations

import json
from pathlib import Path

DIR = Path(__file__).with_name('packs')

# extra takes on the same idea: each one is told to differ in specific ways
VARIANTS = [
    '',
    'This is take 2 of the same idea. Keep the genre and mood, but use a different key and tempo inside the '
    "genre's range, a different drum pattern, a different lead instrument and a different title.",
    'This is take 3, the boldest version. Keep the genre, but try an unexpected arrangement (for example open on '
    'the hook or a breakdown), a new chord progression, different sounds and a different title.',
]
MAX_VERSIONS = len(VARIANTS)


def all_packs() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for f in sorted(DIR.glob('*.json')) if DIR.is_dir() else []:
        try:
            data = json.loads(f.read_text(encoding='utf-8'))
        except ValueError:
            continue
        for p in data if isinstance(data, list) else [data]:
            if isinstance(p, dict) and p.get('id'):
                out[str(p['id'])] = p
    return out


def get(pack: str | None) -> dict | None:
    if not pack:
        return None
    p = all_packs()
    if pack not in p:
        raise KeyError(f'unknown genre pack {pack!r}. Packs: {", ".join(sorted(p))}')
    return p[pack]


def menu() -> list[dict]:
    return [{'id': k, 'name': v.get('name'), 'free': bool(v.get('free')), 'blurb': v.get('blurb', ''),
             'example': v.get('example', '')} for k, v in all_packs().items()]


def brief(pack: dict | None) -> str:
    """Prompt text: the pack's recipe plus its producer style, if any."""
    if not pack:
        return ''
    lines = [f"Genre pack: {pack['name']} ({pack.get('blurb', '')}). Follow this recipe:",
             f"- Use the template \"{pack['template']}\" if it is in the catalog.",
             f"- Tempo {pack['bpm'][0]}-{pack['bpm'][1]} BPM unless the idea names a tempo."]
    lines += [f'- {r}' for r in pack.get('rules', [])]
    if pack.get('style'):
        from . import styles
        try:
            lines.append(styles.brief(styles.get(pack['style'])))
        except KeyError:
            pass
    return '\n'.join(lines)


def variant_note(variant: int) -> str:
    return VARIANTS[variant] if 0 < variant < len(VARIANTS) else ''
