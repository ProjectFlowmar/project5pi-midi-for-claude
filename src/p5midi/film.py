"""Film scoring: narration + scene timeline -> an original underscore, timed to the second.

    p5midi film --srt captions.srt --duration 180 [--regions regions.json] [--video in.mp4] -o out/

1. plan_cues(): an AI "music director" reads the timed narration and the timeline and writes a
   cue sheet: moods with intensity, and the exact moments that deserve a hit (boom, crack, sting,
   swell). Times are checked and gaps filled; if the AI fails, a keyword heuristic plans instead.
2. compose(): turns the cue sheet into notes with a cinematic palette (drones in the film's key,
   pads, strings, choir, glass, plucks, piano, sub pulse, booms, risers, stings). Chords change on
   narration line starts, so the music breathes with the voice. Regions marked intro/silence stay
   silent (an intro plays its own music); title/act cards get a swell and a boom; an end card resolves.
3. score_film(): writes the spec, builds the FL Studio project, renders it, returns score.wav.

Regions: [{"start": s, "end": s, "kind": "intro|silence|title|act|sponsor|end_card"}]. Times in seconds.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import theory

MOODS = ('unease', 'tension', 'question', 'curiosity', 'wonder', 'warmth', 'sadness', 'triumph', 'neutral')
HITS = ('boom', 'crack', 'sting', 'swell')
SAMPLES = Path(os.environ.get('P5MIDI_SAMPLES', 'D:/Music Production/Samples/MusicRadar/Cinematic'))
BPM = 80.0


# ---------------------------------------------------------------- inputs
def read_srt(path: str | Path) -> list[dict]:
    def t(x):
        h, m, r = x.strip().split(':')
        s, ms = r.replace('.', ',').split(',')
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
    out = []
    for block in Path(path).read_text(encoding='utf-8', errors='replace').strip().split('\n\n'):
        rows = block.strip().splitlines()
        if len(rows) >= 3 and '-->' in rows[1]:
            a, b = rows[1].split('-->')
            out.append({'start': round(t(a), 2), 'end': round(t(b), 2), 'text': ' '.join(rows[2:]).strip()})
    return out


def merge_lines(lines: list[dict], gap: float = 0.6) -> list[dict]:
    """Caption fragments -> sentences (a new line starts after a pause or a full stop)."""
    out = []
    for ln in lines:
        if out and ln['start'] - out[-1]['end'] < gap and not re.search(r'[.?!]["\']?$', out[-1]['text']):
            out[-1]['end'] = ln['end']
            out[-1]['text'] += ' ' + ln['text']
        else:
            out.append(dict(ln))
    return out


# ---------------------------------------------------------------- 1. cue sheet
DIRECTOR = """You are the music director for a documentary. You get the narration with times (seconds) and the
timeline regions. Write a cue sheet as JSON:
{"key": "D minor", "cues": [{"start": 0, "end": 12.5, "mood": "unease", "intensity": 2}],
 "hits": [{"t": 28.5, "kind": "swell", "why": "the central question"}]}

Rules:
- moods: unease, tension, question, curiosity, wonder, warmth, sadness, triumph, neutral.
  intensity 1 (barely there) to 5 (full). Documentary music stays under the voice: mostly 1-3.
- Cues cover the narration, in order, no overlaps. Change mood where the story turns (a new era, a
  reveal, a question, bad news, a person's fate). Typical cue length 8-40 s.
- Sponsor or ad reads: mood "warmth", intensity 1.
- hits: at most one per ~20 s, only on real turning points: "crack" for a reveal word, "boom" for a
  big fact or a scene change, "sting" for a cut to a new place or time, "swell" for a big question or
  an emotional peak. Put t exactly on the start time of the line (or the key word) it belongs to.
- key: a minor key that suits the film (A, C, D, E or G minor).
- If the narration is only "[shot N]" markers (a picture-only video), follow the "brief" and give the
  film an arc on those shot times: an inviting start, a build, a peak around two thirds in, a resolve.
  Promos and upbeat briefs use wonder/warmth/triumph more than unease.
Answer with the JSON only."""


def scene_cuts(video: str | Path, threshold: float = 0.3) -> list[float]:
    """Cut times (seconds) from ffmpeg's scene detector."""
    from .analyze import _run
    out = _run(['-i', str(video), '-vf', f"select='gt(scene,{threshold})',showinfo", '-an', '-f', 'null', '-'])
    cuts = sorted({round(float(m), 2) for m in re.findall(r'pts_time:([\d.]+)', out)})
    keep, last = [], -9.0
    for c in cuts:                       # flashes and fast montages: at most one cut per 1.5 s
        if c - last >= 1.5:
            keep.append(c)
            last = c
    return keep


def cut_lines(cuts: list[float], duration: float) -> list[dict]:
    """Picture-only videos: each shot becomes a timed 'line' so chords change on the cuts."""
    pts = [0.0] + [c for c in cuts if 0.5 < c < duration - 0.5] + [duration]
    return [{'start': round(a, 2), 'end': round(b, 2), 'text': f'[shot {i + 1}]'} for i, (a, b) in enumerate(zip(pts, pts[1:]))]


def plan_cues(lines: list[dict], regions: list[dict], duration: float, ai=None, title: str = '', brief: str = '') -> dict:
    """AI cue sheet, validated; falls back to a heuristic plan if the AI is unavailable or wrong."""
    try:
        from .compose import AI, parse_json
        ai = ai or AI.from_env()
        ask = {'title': title, 'brief': brief or 'documentary underscore', 'duration': round(duration, 2),
               'regions': [{k: r[k] for k in ('start', 'end', 'kind')} for r in regions],
               'narration': [{'t': ln['start'], 'end': ln['end'], 'text': ln['text'][:300]} for ln in lines]}
        text, _ = ai.chat([{'role': 'system', 'content': DIRECTOR}, {'role': 'user', 'content': json.dumps(ask)}], think=False)
        sheet = parse_json(text)
        sheet['source'] = 'ai'
    except Exception as e:  # no key, network, bad JSON: never block a render
        sheet = heuristic_cues(lines, regions, duration)
        sheet['source'] = f'heuristic ({type(e).__name__}: {str(e)[:100]})'
    return validate_cues(sheet, lines, regions, duration)


def heuristic_cues(lines: list[dict], regions: list[dict], duration: float) -> dict:
    cues, hits = [], []
    for ln in lines:
        txt = ln['text'].lower()
        mood, inten = 'curiosity', 2
        if '?' in txt:
            mood = 'question'
        elif re.search(r'fear|danger|disaster|accident|war|bomb|death|died|crisis|melt', txt):
            mood, inten = 'tension', 3
        elif re.search(r'nobel|first|breakthrough|record|biggest|largest', txt):
            mood = 'wonder'
        elif re.search(r'left|lost|forced|exile|tragic|sad', txt):
            mood = 'sadness'
        cues.append({'start': ln['start'], 'end': ln['end'], 'mood': mood, 'intensity': inten})
        if re.search(r'crack|split|discovered|proved|exploded', txt):
            hits.append({'t': ln['start'], 'kind': 'crack'})
    return {'key': 'D minor', 'cues': cues, 'hits': hits}


def _region_at(regions, t):
    return next((r for r in regions if r['start'] <= t < r['end']), None)


def validate_cues(sheet: dict, lines: list[dict], regions: list[dict], duration: float) -> dict:
    root, mode = 'D', 'minor'
    try:
        r, m = theory.parse_key(str(sheet.get('key', 'D minor')))
        root = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'][r]
        mode = 'minor' if m in ('minor', 'harmonic_minor', 'dorian', 'phrygian') else 'minor'
    except Exception:
        pass
    if root not in ('A', 'C', 'D', 'E', 'G', 'F', 'B'):
        root = 'D'
    raw = []
    for c in sheet.get('cues') or []:
        try:
            a, b = max(0.0, float(c['start'])), min(duration, float(c['end']))
        except (KeyError, TypeError, ValueError):
            continue
        if b - a < 0.5:
            continue
        mood = c.get('mood') if c.get('mood') in MOODS else 'neutral'
        raw.append({'start': round(a, 2), 'end': round(b, 2), 'mood': mood,
                    'intensity': max(1, min(5, int(c.get('intensity', 2) or 2)))})
    # sponsor reads are always a warm, quiet bed: cut them out of other cues and add them as fixed cues
    for r in (r for r in regions if r['kind'] == 'sponsor'):
        keep = []
        for c in raw:
            if c['end'] <= r['start'] or c['start'] >= r['end']:
                keep.append(c)
                continue
            if c['start'] < r['start']:
                keep.append({**c, 'end': r['start']})
            if c['end'] > r['end']:
                keep.append({**c, 'start': r['end']})
        raw = keep + [{'start': round(r['start'], 2), 'end': round(r['end'], 2), 'mood': 'warmth', 'intensity': 1}]
    raw.sort(key=lambda c: c['start'])
    cues, t = [], 0.0
    for c in raw:                                   # no overlaps; gaps get a neutral bed
        if c['start'] < t:
            c['start'] = t
        if c['end'] - c['start'] < 0.5:
            continue
        if c['start'] - t > 0.5:
            cues.append({'start': round(t, 2), 'end': c['start'], 'mood': 'neutral', 'intensity': 1})
        cues.append(c)
        t = c['end']
    if duration - t > 0.5:
        cues.append({'start': round(t, 2), 'end': round(duration, 2), 'mood': 'neutral', 'intensity': 1})
    merged = []                                     # fold cues shorter than 4 s into the previous one
    for c in cues:
        if merged and c['end'] - c['start'] < 4 and merged[-1]['mood'] != 'neutral':
            merged[-1]['end'] = c['end']
        else:
            merged.append(c)
    hits, last = [], -99.0
    for h in sorted(sheet.get('hits') or [], key=lambda h: float(h.get('t', 0) or 0)):
        try:
            t = float(h['t'])
        except (KeyError, TypeError, ValueError):
            continue
        reg = _region_at(regions, t)
        if not 0.5 <= t < duration - 1 or (reg and reg['kind'] in ('intro', 'silence')) or t - last < 12:
            continue
        hits.append({'t': round(t, 2), 'kind': h.get('kind') if h.get('kind') in HITS else 'swell', 'why': str(h.get('why', ''))[:80]})
        last = t
    return {'key': f'{root} {mode}', 'cues': merged, 'hits': hits, 'source': sheet.get('source', '')}


# ---------------------------------------------------------------- 2. composer
def palette(root: str) -> list[dict]:
    """Kit channels for the cinematic palette; sample layers are skipped if the files are missing."""
    S = SAMPLES
    drone_dir = S / 'Organic Drones' / 'Deconstructed One Shots' / root
    drones = sorted(drone_dir.glob('*.wav')) if drone_dir.is_dir() else []
    kit = []
    if drones:
        kit.append({'name': 'Drone A', 'sample': str(drones[0]), 'root': f'{root}4'})
        kit.append({'name': 'Drone B', 'sample': str(drones[min(2, len(drones) - 1)]), 'root': f'{root}4'})
    for name, rel in (('Boom', 'Big Impacts/Low End/Low End 03.wav'), ('Boom Short', 'Big Impacts/Low End/Low End 01.wav'),
                      ('Sting', 'Cinematic Drama/Stings/Atmos Sting 01.wav')):
        if (S / rel).is_file():
            kit.append({'name': name, 'sample': str(S / rel)})
    kit += [{'name': 'Glass', 'from': 'Patch bank', 'channel': 'Glass'},
            {'name': 'Pluck', 'from': 'Patch bank', 'channel': 'Pluck'},
            {'name': 'Choir ahh', 'from': 'Patch bank', 'channel': 'Choir ahh'}]
    return kit


class Writer:
    def __init__(self, key: str):
        self.key = key
        self.parts: dict[str, list] = {}
        r, _ = theory.parse_key(key)
        self.root_pc = r

    def note(self, part, t, pitch, dur, vel):
        b = BPM / 60.0
        self.parts.setdefault(part, []).append([round(t * b, 3), int(pitch), round(max(0.05, dur) * b, 3), int(max(1, min(127, vel)))])

    def chord(self, part, t, pitches, dur, vel):
        for p in pitches:
            self.note(part, t, p, dur, vel)

    def triad(self, deg: str, octave: int) -> list[int]:
        return theory.voice_lead([theory.chord_from_roman(deg, self.key, octave)], center=12 * (octave + 1) + 4)[0]


PROGS = {  # roman numerals per mood (cycled), key-relative
    'unease': ['i', 'VI', 'iv', 'V'], 'tension': ['i', 'VI', 'iv', 'Vsus4'], 'question': ['VIadd9', 'IVsus2', 'Vsus4'],
    'curiosity': ['i', 'VI', 'III', 'VII'], 'wonder': ['III', 'VII', 'i', 'VI'], 'warmth': ['III', 'VII', 'i', 'VI'],
    'sadness': ['i', 'iv', 'VI', 'V'], 'triumph': ['VI', 'VII', 'III', 'i'], 'neutral': ['i', 'VI'],
}


def _change_points(a: float, b: float, lines: list[dict], every: float = 7.0) -> list[float]:
    """Chord changes near every `every` seconds, snapped to the nearest narration line start."""
    starts = [ln['start'] for ln in lines if a < ln['start'] < b - 2]
    pts, t = [a], a
    while t + every < b - 2:
        target = t + every
        near = min(starts, key=lambda s: abs(s - target)) if starts else target
        t = near if abs(near - target) < every * 0.45 and near > t + 2.5 else target
        pts.append(round(t, 2))
    return pts


def compose(sheet: dict, lines: list[dict], regions: list[dict], duration: float, title: str = 'Film score') -> dict:
    """Cue sheet -> a p5midi song spec (template Chillout-Ambient + cinematic kit)."""
    key = sheet['key']
    root = key.split()[0]
    w = Writer(key)
    kit = palette(root)
    have = {k['name'] for k in kit}
    silent = [(r['start'], r['end']) for r in regions if r['kind'] in ('intro', 'silence')]

    def muted(a, b):
        return any(a < sb and b > sa for sa, sb in silent)

    def clip(a, b):  # trim a span so it never plays inside an intro / silence region
        for sa, sb in silent:
            if a < sa < b:
                b = sa - 0.3
            if a < sb <= b and a >= sa:
                a = sb
        return a, b

    for c in sheet['cues']:
        a, b = clip(c['start'], c['end'])
        if b - a < 1 or muted(a + 0.1, a + 0.2):
            continue
        mood, n = c['mood'], c['intensity']
        prog = PROGS[mood]
        pts = _change_points(a, b, lines, every=6.5 if mood in ('tension', 'unease') else 7.5)
        reg = _region_at(regions, a + 0.1)
        sponsor = reg is not None and reg['kind'] == 'sponsor'
        if 'Drone A' in have and mood in ('unease', 'tension', 'question', 'curiosity', 'neutral') and not sponsor:
            w.note('drone', a, theory.note_number(f'{root}4'), min(b - a, 29.0), 58 + 8 * n)   # drone at its own pitch
        for i, t in enumerate(pts):
            end = pts[i + 1] if i + 1 < len(pts) else b
            deg = prog[i % len(prog)]
            low = w.triad(deg, 3)
            if sponsor:
                w.chord('pad2', t, low, end - t, 46)
                top = [p + 12 for p in low]
                k = 0
                while t + 0.4 + k * 1.5 < end - 0.3:
                    w.note('piano', t + 0.4 + k * 1.5, top[k % 3] + 12, 1.2, 70)
                    k += 1
                continue
            w.chord('pad', t, low, end - t, 48 + 6 * n)
            if mood in ('tension', 'unease') and n >= 2:                     # heartbeat pulse, building through the cue
                k, steps = 0, max(1, int((end - t) / 0.75))
                while k < steps:
                    w.note('sub', t + k * 0.75, low[0] - 24, 0.45, 40 + 12 * n * (0.5 + 0.5 * (t + k * 0.75 - a) / max(1, b - a)))
                    k += 1
            if mood in ('curiosity', 'wonder', 'triumph'):                   # glass arpeggio
                arp = [low[0] + 24, low[2] + 24, low[1] + 24, low[2] + 24] if mood != 'wonder' else [low[0] + 24, low[1] + 24, low[2] + 24, low[0] + 36]
                k = 0
                while t + k * 0.375 < end - 0.1:
                    w.note('glass', t + k * 0.375, arp[k % 4], 0.34, 40 + 6 * n)
                    k += 1
            if mood == 'tension' and n >= 3:
                k = 0
                while t + k * 0.375 < end - 0.1:
                    w.note('pluck', t + k * 0.375, [low[0] + 12, low[1] + 12, low[2] + 12, low[1] + 12][k % 4], 0.3, 22 + 4 * n)
                    k += 1
            if n >= 3 or mood in ('question', 'triumph', 'wonder'):          # strings on top
                w.chord('strings', t, [low[1] + 12, low[2] + 12], end - t, 46 + 7 * n)
            if mood in ('triumph',) or (mood == 'wonder' and n >= 4):
                w.chord('choir', t, [low[0] + 12, low[2] + 12], end - t, 44 + 6 * n)
            if mood == 'sadness':                                            # a falling piano line on chord tones
                tones = sorted({p + 12 for p in low} | {p + 24 for p in low})
                k, j = 0, len(tones) - 1
                while t + k * 0.75 < end - 0.4:
                    w.note('piano', t + k * 0.75, tones[j % len(tones)], 0.7, 92 - 4 * (k % 4))
                    j -= 1 if k % 3 != 2 else -1
                    k += 1
            if mood == 'question' and i == 0:
                w.note('piano', t + 0.5, low[2] + 24, 2.5, 80)

    # cards and the end
    for r in regions:
        if r['kind'] in ('title', 'act'):
            t = r['start']
            if 'Boom' in have:
                w.note('boom', t, 60, 5.0, 100)
            if t > 2.4 and not muted(t - 2.4, t - 2.3):
                w.note('riser', t - 2.35, 60, 2.35, 72)
            w.chord('strings', t, w.triad('i', 3) + [p + 12 for p in w.triad('i', 3)], r['end'] - r['start'] + 0.3, 76)
            w.chord('choir', t, [p + 12 for p in w.triad('i', 3)][:2], r['end'] - r['start'] + 0.3, 58)
        elif r['kind'] == 'end_card':
            t = r['start']
            w.chord('strings', t, w.triad('i', 3) + [p + 12 for p in w.triad('i', 3)], r['end'] - t, 66)
            w.chord('pad', t, w.triad('i', 3), r['end'] - t, 56)
            w.chord('choir', t, [p + 12 for p in w.triad('i', 3)][:2], r['end'] - t, 50)
    for h in sheet['hits']:
        t, kind = h['t'], h['kind']
        if kind in ('boom', 'crack') and 'Boom' in have:
            if kind == 'crack' and t > 2.4:
                w.note('riser', t - 2.35, 60, 2.35, 74)
            w.note('boom2' if kind == 'crack' and 'Boom Short' in have else 'boom', t, 60, 3.0, 104 if kind == 'crack' else 92)
        elif kind == 'sting' and 'Sting' in have:
            w.note('sting', t, 60, 5.5, 100)
        elif kind == 'swell':
            if t > 2.4:
                w.note('riser', t - 2.35, 60, 2.35, 66)
            w.chord('strings', t, [p + 12 for p in w.triad('i', 3)], 4.0, 74)

    ch = {'drone': 'Drone A', 'boom': 'Boom', 'boom2': 'Boom Short', 'sting': 'Sting', 'riser': 'Reverse Crash', 'pad': 'Pad 1',
          'pad2': 'Pad 2', 'piano': 'Piano', 'strings': 'Strings', 'sub': 'Sub 1', 'glass': 'Glass', 'pluck': 'Pluck', 'choir': 'Choir ahh'}
    mult = {'pluck': 0.45, 'piano': 1.0, 'riser': 1.7, 'sting': 1.0, 'pad2': 1.4}
    bars = int(duration * BPM / 60 / 4) + 2
    parts = {k: {'type': 'notes', 'channel': ch[k], 'bars': bars, 'notes': sorted(v), 'velocity': mult.get(k, 1.0)}
             for k, v in w.parts.items() if ch[k] in have or ch[k] in ('Reverse Crash', 'Pad 1', 'Pad 2', 'Piano', 'Strings', 'Sub 1')}
    return {'title': title[:80], 'comment': 'Original underscore made with Project5pi MIDI for Claude.',
            'template': 'Chillout-Ambient', 'bpm': BPM, 'key': key, 'kit': {'donor': 'Basic', 'channels': kit},
            'parts': parts, 'arrangement': [{'name': 'score', 'bars': bars, 'parts': list(parts)}]}


# ---------------------------------------------------------------- 3. render
def score_film(lines: list[dict], regions: list[dict], duration: float, out_dir: str | Path, title: str = 'Film score',
               ai=None, render: bool = True, step=lambda s: None, brief: str = '') -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    lines = merge_lines(lines)
    step('planning cues')
    sheet = plan_cues(lines, regions, duration, ai=ai, title=title, brief=brief)
    (out / 'cue-sheet.json').write_text(json.dumps(sheet, indent=1), encoding='utf-8')
    spec = compose(sheet, lines, regions, duration, title)
    (out / 'score-spec.json').write_text(json.dumps(spec, indent=1), encoding='utf-8')
    from .song import build
    step('building the FL Studio project')
    res = build(spec)
    flp = out / 'score.flp'
    res.project.save(flp)
    result = {'folder': str(out.resolve()), 'flp': str(flp), 'cue_sheet': sheet, 'parts': list(spec['parts'])}
    if render:
        from .fl import render as fl_render
        step('rendering in FL Studio')
        result['wav'] = str(fl_render(flp, 'wav'))
    return result


def regions_from_scenes(scenes: list[dict]) -> list[dict]:
    """docv2-style scene list -> regions (intro, title, act, sponsor, end_card)."""
    kinds = {'intro': 'intro', 'title': 'title', 'act': 'act', 'lc': 'sponsor', 'spot': 'sponsor', 'end': 'end_card'}
    return [{'start': float(s['start']), 'end': float(s['end']), 'kind': kinds[s['type']]} for s in scenes if s.get('type') in kinds]


def mix_preview(video: str | Path, score_wav: str | Path, out: str | Path, music_lufs: float = -27.0) -> Path:
    """Put a score under a video's existing audio: the music ducks whenever the video's audio is speaking,
    then the whole mix goes to -14 LUFS (YouTube). For videos without a pipeline of their own."""
    from .analyze import _run, loudness
    music_gain = music_lufs - (loudness(score_wav)['lufs'] or -20.0)
    fc = (f"[1:a]volume={music_gain:.2f}dB,aresample=48000[m];[0:a]aresample=48000,asplit=2[v][key];"
          f"[m][key]sidechaincompress=threshold=0.015:ratio=8:attack=30:release=700:makeup=1[md];"
          f"[v][md]amix=inputs=2:normalize=0:duration=first,loudnorm=I=-14:TP=-1.5:LRA=11[a]")
    _run(['-y', '-i', str(video), '-i', str(score_wav), '-filter_complex', fc, '-map', '0:v', '-map', '[a]',
          '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', str(out)])
    return Path(out)
