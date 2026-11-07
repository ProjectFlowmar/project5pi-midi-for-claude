"""Measure a render so an AI (which cannot hear) gets numbers instead of guesses.

Everything here shells out to ffmpeg. Reports loudness (LUFS), true peak, dynamic
range, a six-band tonal balance, stereo width and per-section levels, and can
compare against a reference track you like.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .fl import ffmpeg

BANDS = [  # name, low Hz, high Hz
    ('sub', 20, 60), ('bass', 60, 250), ('low_mid', 250, 1000),
    ('mid', 1000, 4000), ('presence', 4000, 8000), ('air', 8000, 20000),
]
_NOWIN = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def _run(args: list[str]) -> str:
    p = subprocess.run([ffmpeg(), '-hide_banner', '-nostats', *args], capture_output=True, text=True,
                       encoding='utf-8', errors='replace', creationflags=_NOWIN)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-800:])
    return p.stderr


def _seg(start, end) -> list[str]:
    a = []
    if start is not None:
        a += ['-ss', f'{start:.3f}']
    if end is not None:
        a += ['-to', f'{end:.3f}']
    return a


def _rms(path, af: str, start=None, end=None) -> float | None:
    out = _run([*_seg(start, end), '-i', str(path), '-af', f'{af},astats=measure_perchannel=none',
                '-f', 'null', '-'])
    m = re.findall(r'RMS level dB:\s*(-?[\d.]+|-inf)', out)
    if not m or m[-1] == '-inf':
        return None
    return round(float(m[-1]), 1)


def loudness(path, start=None, end=None) -> dict:
    out = _run([*_seg(start, end), '-i', str(path), '-af', 'ebur128=peak=true', '-f', 'null', '-'])
    summary = out[out.rfind('Summary:'):]

    def grab(label):
        m = re.search(label + r':\s*(-?[\d.]+|-inf)', summary)
        return None if not m or m.group(1) == '-inf' else float(m.group(1))
    return {'lufs': grab('I'), 'lra': grab('LRA'), 'true_peak_db': grab('Peak')}


def duration(path) -> float:
    out = _run(['-i', str(path), '-f', 'null', '-'])
    m = re.findall(r'time=(\d+):(\d+):([\d.]+)', out) or re.findall(r'Duration: (\d+):(\d+):([\d.]+)', out)
    h, mi, s = m[-1]
    return round(int(h) * 3600 + int(mi) * 60 + float(s), 2)


def tonal_balance(path, start=None, end=None) -> dict:
    """RMS level (dBFS) per band. Compare shapes, not single numbers."""
    res = {}
    for name, lo, hi in BANDS:
        res[name] = _rms(path, f'highpass=f={lo}:poles=2,lowpass=f={hi}:poles=2', start, end)
    return res


def stereo(path, start=None, end=None) -> dict:
    mid = _rms(path, 'pan=mono|c0=0.5*c0+0.5*c1', start, end)
    side = _rms(path, 'pan=mono|c0=0.5*c0-0.5*c1', start, end)
    width = None if mid is None or side is None else round(side - mid, 1)
    low_side = _rms(path, 'lowpass=f=120,pan=mono|c0=0.5*c0-0.5*c1', start, end)
    return {'side_minus_mid_db': width, 'low_end_side_db': low_side}


def analyze(path, sections: list[dict] | None = None, reference=None) -> dict:
    path = Path(path)
    rep = {'file': str(path), 'duration_s': duration(path), **loudness(path)}
    rep['bands_db'] = tonal_balance(path)
    rep['stereo'] = stereo(path)
    notes = []
    if rep['true_peak_db'] is not None and rep['true_peak_db'] > -0.1:
        notes.append(f"Clipping risk: true peak {rep['true_peak_db']} dBFS. Run `master` or lower channel volumes.")
    if rep['lufs'] is not None and rep['lufs'] < -18:
        notes.append(f"Quiet render ({rep['lufs']} LUFS). Streaming-ready masters sit around -14 to -9 LUFS.")
    b = rep['bands_db']
    lows = [v for v in (b['sub'], b['bass']) if v is not None]
    if lows and b['mid'] is not None and max(lows) - b['mid'] > 10:
        notes.append(f"Very bass-heavy: lows are {max(lows) - b['mid']:.0f} dB above the mids (1-4 kHz). "
                     "Melody and chords may be buried; raise them or lower the bass/808.")
    if b['mid'] is not None and b['presence'] is not None and b['presence'] - b['mid'] > 6:
        notes.append('Harsh top: 4-8 kHz is well above the mids; tame hats or bright synths.')
    w = rep['stereo']['side_minus_mid_db']
    if w is not None and w < -25:
        notes.append('Mix is nearly mono; consider panning or stereo effects on pads/hats.')
    ls = rep['stereo']['low_end_side_db']
    if ls is not None and ls > -30:
        notes.append('Low end has stereo content; bass and kick usually sit best in mono.')
    if sections:
        rep['sections'] = []
        for s in sections:
            lv = loudness(path, s['start_s'], s['end_s'])
            rep['sections'].append({'name': s['name'], 'start_s': s['start_s'], 'end_s': s['end_s'],
                                    'lufs': lv['lufs'], 'parts': s.get('parts')})
            if lv['lufs'] is None or lv['lufs'] < -45:
                notes.append(f"Section {s['name']!r} is silent or near-silent; check its channels make sound.")
        secs = [s for s in rep['sections'] if s['lufs'] is not None and s.get('parts')]
        for a in secs:  # a thinner section should not be louder than a fuller one
            for b in secs:
                if set(a['parts']) < set(b['parts']) and a['lufs'] - b['lufs'] > 1.5:
                    extra = sorted(set(a['parts']) & set(b['parts']))
                    notes.append(f"Section {a['name']!r} ({', '.join(a['parts'])}) is {a['lufs'] - b['lufs']:.1f} LU "
                                 f"louder than the fuller {b['name']!r}. Likely too loud: one of {', '.join(extra)}.")
                    break
    if reference:
        ref = Path(reference)
        rl = loudness(ref)
        rb = tonal_balance(ref)
        # compare shape: normalise both curves to their own loudest band
        def norm(b):
            vals = [v for v in b.values() if v is not None]
            top = max(vals) if vals else 0
            return {k: (None if v is None else round(v - top, 1)) for k, v in b.items()}
        mine, theirs = norm(rep['bands_db']), norm(rb)
        diff = {k: (None if mine[k] is None or theirs[k] is None else round(mine[k] - theirs[k], 1)) for k in mine}
        rep['reference'] = {'file': str(ref), 'lufs': rl['lufs'], 'true_peak_db': rl['true_peak_db'],
                            'band_shape_difference_db': diff}
        for k, d in diff.items():
            if d is not None and abs(d) >= 4:
                notes.append(f"{k.replace('_', ' ')} is {abs(d)} dB {'louder' if d > 0 else 'quieter'} "
                             f"than the reference (relative to each mix's loudest band).")
        if rl['lufs'] is not None and rep['lufs'] is not None:
            notes.append(f"Loudness: yours {rep['lufs']} LUFS vs reference {rl['lufs']} LUFS.")
    rep['notes'] = notes
    return rep


def master(src, dst, lufs: float = -10.0, ceiling_db: float = -1.0, bitrate: str = '320k') -> dict:
    """Bring a render to a target loudness with one static gain and a brickwall limiter.

    Static gain keeps the song's dynamics (sections stay in proportion), unlike
    ffmpeg's loudnorm, which rides the volume over time. The gain is corrected
    against the measured result of the encoded file (up to 5 passes) until the
    loudness is within 0.5 LU of target and the true peak is under the ceiling.
    Output format follows the dst extension (mp3, wav, flac).
    """
    src, dst = Path(src), Path(dst)
    start = loudness(src)
    if start['lufs'] is None:
        raise RuntimeError(f'{src} is silent; nothing to master')
    gain = lufs - start['lufs']
    limit = 10 ** ((ceiling_db - 0.7) / 20)
    res = {}
    for _ in range(5):
        af = (f"volume={gain:.2f}dB,alimiter=limit={limit:.4f}:level=false:attack=1:release=50:asc=1,"
              f"aresample=44100")
        args = ['-y', '-i', str(src), '-af', af]
        if dst.suffix.lower() == '.mp3':
            args += ['-c:a', 'libmp3lame', '-b:a', bitrate]
        elif dst.suffix.lower() == '.wav':
            args += ['-c:a', 'pcm_s24le']
        _run([*args, str(dst)])
        res = loudness(dst)
        over = res['true_peak_db'] is not None and res['true_peak_db'] > ceiling_db
        miss = lufs - res['lufs']
        if over:
            limit *= 10 ** ((ceiling_db - res['true_peak_db'] - 0.2) / 20)
        elif abs(miss) > 0.5:
            gain += miss
        else:
            break
    res.update({'file': str(dst), 'target_lufs': lufs, 'gain_db': round(gain, 2),
                'input_lufs': start['lufs']})
    if abs(lufs - res['lufs']) > 0.5:
        res['warning'] = (f"reached {res['lufs']} LUFS, not {lufs}: the mix is too dynamic or peaky to go that "
                          "loud cleanly. Lower the loudest part or accept the quieter master.")
    return res
