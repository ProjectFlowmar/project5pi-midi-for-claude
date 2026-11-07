"""Free-license music for short videos (Omar 11/7: under 3 minutes use free-license audio, longer gets an FL Studio score).

Source: Mixkit (Mixkit Stock Music Free License: free for commercial use in videos, no attribution; don't
redistribute the tracks on their own). Tracks are read from the structured data (JSON-LD) on Mixkit's tag
pages, which carries each track's name, artist, length, MP3 address and license. Never from the visual
cards: their titles are offset from the MP3s.

    pick_track(brief, duration)  -> track dict (AI picks from candidates; falls back to the best length match)
    fetch(track)                 -> local mp3 in the library, license line recorded
    fit(mp3, duration, out_wav)  -> trimmed with a fade-out, or looped with a crossfade if too short
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from pathlib import Path

LIBRARY = Path(os.environ.get('P5MIDI_FREE_MUSIC', 'D:/Music Production/Free Music/Mixkit'))
LICENSE_URL = 'https://mixkit.co/license/#musicFree'
TAGS = ['cinematic', 'corporate', 'upbeat', 'inspiring', 'happy', 'chill', 'hip-hop', 'ambient', 'suspense', 'epic',
        'sad', 'electronic', 'piano', 'motivational', 'acoustic', 'funk', 'romantic', 'relaxing', 'rock', 'pop']
UA = {'user-agent': 'Mozilla/5.0 (p5midi music library)'}


def _iso_seconds(d: str) -> float:
    m = re.fullmatch(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?', d or '')
    if not m:
        return 0.0
    h, mi, s = (float(x or 0) for x in m.groups())
    return h * 3600 + mi * 60 + s


def parse_tag_page(html: str, tag: str) -> list[dict]:
    out = []
    for blk in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
        try:
            d = json.loads(blk)
        except ValueError:
            continue
        for node in d.get('@graph', [d]) if isinstance(d, dict) else []:
            for it in node.get('itemListElement') or []:
                if it.get('@type') != 'MusicRecording' or not str(it.get('url', '')).endswith('.mp3'):
                    continue
                m = re.search(r'/music/(\d+)/', it['url'])
                if not m or 'mixkit.co/license' not in str(it.get('license', '')):
                    continue
                out.append({'id': int(m.group(1)), 'name': it.get('name', ''), 'artist': it.get('byArtist', ''),
                            'genre': it.get('genre', ''), 'seconds': _iso_seconds(it.get('duration', '')), 'url': it['url'],
                            'tags': [tag], 'license': it.get('license')})
    return out


def catalog(refresh_days: float = 7, tags: list[str] | None = None) -> list[dict]:
    """All tracks from the tag pages, cached in the library for a week."""
    LIBRARY.mkdir(parents=True, exist_ok=True)
    cache = LIBRARY / 'catalog.json'
    if cache.exists() and time.time() - cache.stat().st_mtime < refresh_days * 86400:
        return json.loads(cache.read_text(encoding='utf-8'))
    by_id: dict[int, dict] = {}
    for tag in tags or TAGS:
        try:
            req = urllib.request.Request(f'https://mixkit.co/free-stock-music/tag/{tag}/', headers=UA)
            html = urllib.request.urlopen(req, timeout=30).read().decode('utf-8', 'replace')
        except Exception:
            continue
        for t in parse_tag_page(html, tag):
            if t['id'] in by_id:
                by_id[t['id']]['tags'] = sorted(set(by_id[t['id']]['tags']) | {tag})
            else:
                by_id[t['id']] = t
        time.sleep(0.4)
    tracks = sorted(by_id.values(), key=lambda t: t['id'])
    if tracks:
        cache.write_text(json.dumps(tracks, indent=1), encoding='utf-8')
    elif cache.exists():
        return json.loads(cache.read_text(encoding='utf-8'))
    return tracks


def _used() -> dict[str, float]:
    f = LIBRARY / 'used.json'
    return json.loads(f.read_text(encoding='utf-8')) if f.exists() else {}


PICKER = """You pick background music for a short video. Given the brief and a list of free tracks (id, name,
genre, tags, seconds), answer JSON {"id": <track id>, "why": "<few words>"}. Choose the track whose mood
fits the brief best. Prefer tracks at least as long as the video; avoid ones marked recently_used."""


def pick_track(brief: str, duration: float, ai=None, tracks: list[dict] | None = None) -> dict:
    tracks = tracks if tracks is not None else catalog()
    if not tracks:
        raise RuntimeError('no free-license tracks available (Mixkit unreachable and no cached catalog)')
    used = _used()
    recent = {int(k) for k, t in used.items() if time.time() - t < 30 * 86400}
    words = set(re.findall(r'[a-z\-]+', brief.lower()))
    def score(t):
        s = 3 * len(words & set(t['tags'])) + (2 if words & set(t['genre'].lower().split()) else 0)
        s += 2 if t['seconds'] >= duration else -2 * (duration - t['seconds']) / max(duration, 1)
        return s - (4 if t['id'] in recent else 0)
    ranked = sorted(tracks, key=score, reverse=True)
    choice, why = ranked[0], 'best tag and length match'
    try:
        from .compose import AI, parse_json
        ai = ai or AI.from_env()
        cands = [{'id': t['id'], 'name': t['name'], 'genre': t['genre'], 'tags': t['tags'], 'seconds': round(t['seconds']),
                  'recently_used': t['id'] in recent} for t in ranked[:40]]
        text, _ = ai.chat([{'role': 'system', 'content': PICKER},
                           {'role': 'user', 'content': json.dumps({'brief': brief, 'video_seconds': round(duration), 'tracks': cands})}],
                          think=False)
        ans = parse_json(text)
        hit = next((t for t in ranked[:40] if t['id'] == int(ans.get('id', -1))), None)
        if hit:
            choice, why = hit, str(ans.get('why', ''))[:120]
    except Exception:
        pass
    return {**choice, 'why': why}


def fetch(track: dict, used_for: str = '') -> Path:
    LIBRARY.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r'[^A-Za-z0-9 ._-]+', '', f"{track['id']} - {track['name']} - {track['artist']}")[:120]
    dst = LIBRARY / f'{safe}.mp3'
    if not dst.exists():
        req = urllib.request.Request(track['url'], headers=UA)
        data = urllib.request.urlopen(req, timeout=60).read()
        if len(data) < 10000:
            raise RuntimeError(f"download looked wrong for track {track['id']}")
        dst.write_bytes(data)
    with open(LIBRARY / 'LICENSES.txt', 'a', encoding='utf-8') as f:
        f.write(f"{time.strftime('%Y-%m-%d')}\t{track['name']} - {track['artist']} (mixkit {track['id']})\t"
                f"Mixkit Stock Music Free License {LICENSE_URL}\tused for: {used_for}\n")
    used = _used()
    used[str(track['id'])] = time.time()
    (LIBRARY / 'used.json').write_text(json.dumps(used), encoding='utf-8')
    return dst


def fit(src: str | Path, duration: float, out: str | Path, fade_out: float = 2.5) -> Path:
    """Make the track exactly `duration` long: trim with a fade-out, or loop with a 3 s crossfade first."""
    from .analyze import _run, duration as media_duration
    length = media_duration(src)
    src = str(src)
    if length < duration + 0.5:              # loop: crossfade copies end to start until long enough
        tmp = str(out) + '.loop.wav'
        n = int(duration // max(1.0, length - 3)) + 1
        inputs, chain = [], ''
        for i in range(n + 1):
            inputs += ['-i', src]
        prev = '0:a'
        for i in range(1, n + 1):
            chain += f'[{prev}][{i}:a]acrossfade=d=3:c1=tri:c2=tri[x{i}];'
            prev = f'x{i}'
        _run(['-y', *inputs, '-filter_complex', chain.rstrip(';'), '-map', f'[{prev}]', tmp])
        src = tmp
    f = max(0.5, min(fade_out, duration / 4))
    from .analyze import loudness
    gain = -16.0 - (loudness(src)['lufs'] or -16.0)     # stock tracks are mastered hot: one level, peaks held under -1.5 dB
    _run(['-y', '-i', src, '-af', f'volume={gain:.2f}dB,alimiter=limit=0.84:level=false,'
          f'afade=t=in:d=0.3,afade=t=out:st={duration - f:.3f}:d={f:.3f},aresample=48000',
          '-t', f'{duration:.3f}', '-c:a', 'pcm_s16le', str(out)])
    if src.endswith('.loop.wav'):
        os.remove(src)
    return Path(out)
