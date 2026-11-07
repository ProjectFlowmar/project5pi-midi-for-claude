"""Music helpers: note names, keys, chord progressions, drum grids, humanizing.

Pitch convention: MIDI numbers, C4 = 60 (middle C). FL Studio labels the same
note "C5" in its piano roll; the sound is identical.
"""
from __future__ import annotations

import random
import re

NOTE_INDEX = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
SCALES = {
    'major': [0, 2, 4, 5, 7, 9, 11],
    'minor': [0, 2, 3, 5, 7, 8, 10],          # natural minor / aeolian
    'harmonic_minor': [0, 2, 3, 5, 7, 8, 11],
    'melodic_minor': [0, 2, 3, 5, 7, 9, 11],
    'dorian': [0, 2, 3, 5, 7, 9, 10],
    'phrygian': [0, 1, 3, 5, 7, 8, 10],
    'lydian': [0, 2, 4, 6, 7, 9, 11],
    'mixolydian': [0, 2, 4, 5, 7, 9, 10],
    'locrian': [0, 1, 3, 5, 6, 8, 10],
}
PENTATONIC = {'major': [0, 2, 4, 7, 9], 'minor': [0, 3, 5, 7, 10]}

# General MIDI drum notes. FL's FPC and most drum plugins follow this layout.
GM_DRUMS = {
    'kick': 36, 'kick2': 35, 'rim': 37, 'snare': 38, 'clap': 39, 'snare2': 40,
    'tom_low': 41, 'hat': 42, 'chh': 42, 'tom_mid': 45, 'hat_pedal': 44, 'ohh': 46, 'open_hat': 46,
    'tom_high': 48, 'crash': 49, 'ride': 51, 'china': 52, 'ride_bell': 53, 'tamb': 54,
    'splash': 55, 'cowbell': 56, 'crash2': 57, 'shaker': 70, 'perc': 75, 'clave': 75,
}

ROMAN = {'i': 1, 'ii': 2, 'iii': 3, 'iv': 4, 'v': 5, 'vi': 6, 'vii': 7}


class TheoryError(ValueError):
    pass


def note_number(n) -> int:
    """'C4' -> 60, 'F#2' -> 42, 'Bb3' -> 58, 60 -> 60."""
    if isinstance(n, (int, float)):
        return int(n)
    m = re.fullmatch(r'\s*([A-Ga-g])([#b]*)(-?\d+)\s*', str(n))
    if not m:
        raise TheoryError(f'bad note name {n!r} (use like C4, F#2, Bb3 or a MIDI number)')
    letter, acc, octave = m.groups()
    v = NOTE_INDEX[letter.upper()] + acc.count('#') - acc.count('b')
    return 12 * (int(octave) + 1) + v


def parse_key(key: str) -> tuple[int, str]:
    """'A minor' -> (9, 'minor'); 'F# dorian' -> (6, 'dorian'); 'Eb' -> (3, 'major')."""
    parts = key.replace('-', ' ').split()
    if not parts:
        raise TheoryError('empty key')
    m = re.fullmatch(r'([A-Ga-g])([#b]?)(m?)', parts[0])
    if not m:
        raise TheoryError(f'bad key {key!r} (use like "A minor" or "C major")')
    root = (NOTE_INDEX[m.group(1).upper()] + (1 if m.group(2) == '#' else -1 if m.group(2) == 'b' else 0)) % 12
    mode = parts[1].lower() if len(parts) > 1 else ('minor' if m.group(3) else 'major')
    mode = {'min': 'minor', 'maj': 'major', 'aeolian': 'minor', 'ionian': 'major'}.get(mode, mode)
    if mode not in SCALES:
        raise TheoryError(f'unknown mode {mode!r}; choose from {", ".join(SCALES)}')
    return root, mode


def scale_notes(key: str, low: int = 0, high: int = 127) -> list[int]:
    root, mode = parse_key(key)
    pcs = {(root + s) % 12 for s in SCALES[mode]}
    return [n for n in range(low, high + 1) if n % 12 in pcs]


def degree_to_note(key: str, degree: int, octave: int = 4) -> int:
    """Scale degree (1-based, may go past 7 or below 1) to a MIDI note."""
    root, mode = parse_key(key)
    sc = SCALES[mode]
    d = degree - 1
    return 12 * (octave + 1) + root + sc[d % 7] + 12 * (d // 7)


def chord_from_roman(symbol: str, key: str, octave: int = 4) -> list[int]:
    """Diatonic chord from a roman numeral, e.g. 'i', 'VI', 'iv7', 'V9', 'bVII', 'IIsus4', 'v/add9'.

    Chord quality follows the key (so 'VI' and 'vi' are the same chord in A minor);
    use a 'b' or '#' prefix for borrowed roots. Suffixes: 7, 9, 11, sus2, sus4, add9,
    and 'maj'/'min' to force the third.
    """
    m = re.fullmatch(r'([b#]?)([ivIV]+)(.*)', symbol.strip())
    if not m or m.group(2).lower() not in ROMAN:
        raise TheoryError(f'bad chord symbol {symbol!r} (use roman numerals like i, IV, V7)')
    acc, num, suffix = m.groups()
    suffix = suffix.lower().replace('/', '')
    deg = ROMAN[num.lower()]
    root, mode = parse_key(key)
    sc = SCALES[mode]
    base = 12 * (octave + 1) + root

    def step(n):  # n-th scale step above the chord root (0 = root)
        d = deg - 1 + n
        return base + sc[d % 7] + 12 * (d // 7)

    r = step(0) + (1 if acc == '#' else -1 if acc == 'b' else 0)
    third, fifth = step(2) - step(0), step(4) - step(0)
    if acc:  # borrowed chord: use the case of the numeral for quality
        third = 4 if num.isupper() else 3
        fifth = 7
    if 'maj' in suffix and 'maj7' not in suffix:
        third = 4
    if 'min' in suffix or suffix.startswith('m'):
        third = 3
    if 'dim' in suffix:
        third, fifth = 3, 6
    if 'sus2' in suffix:
        third = 2
    if 'sus4' in suffix:
        third = 5
    notes = [r, r + third, r + fifth]
    if 'maj7' in suffix:
        notes.append(r + 11)
    elif '7' in suffix or '9' in suffix or '11' in suffix:
        notes.append(r + (step(6) - step(0) if not acc else 10))
    if '9' in suffix:
        notes.append(r + 14 if 'add9' in suffix or acc else r + (step(8) - step(0)))
    if '11' in suffix:
        notes.append(r + (step(10) - step(0) if not acc else 17))
    return notes


def voice_lead(chords: list[list[int]], center: int = 64) -> list[list[int]]:
    """Re-voice each chord (inversions / octave moves) to move as little as possible."""
    out: list[list[int]] = []
    prev = None
    for ch in chords:
        pcs: list[int] = []
        for n in ch:
            if n % 12 not in pcs:
                pcs.append(n % 12)
        size = len(pcs)
        best, best_cost = None, None
        target = prev if prev else [center - 5 + 4 * i for i in range(size)]
        for inv in range(len(pcs)):
            order = pcs[inv:] + pcs[:inv]
            for base_oct in range(2, 7):
                v, last = [], None
                for p in order:
                    n = 12 * base_oct + p
                    while last is not None and n <= last:
                        n += 12
                    v.append(n)
                    last = n
                v = v[:size]
                if len(v) < size:
                    continue
                cost = sum(abs(a - b) for a, b in zip(sorted(v), sorted(target)))
                cost += abs(sum(v) / len(v) - center) * 0.5
                if best_cost is None or cost < best_cost:
                    best, best_cost = v, cost
        out.append(best)
        prev = best
    return out


def parse_grid(grid: str, steps_per_bar: int = 16) -> list[tuple[int, int, int]]:
    """Step string -> [(step, length_in_steps, velocity)].

    Characters: X = accent (120), x = hit (100), o = soft (70), g = ghost (45),
    - = hold the previous hit one more step, . or _ = rest. Spaces and | are ignored.
    """
    s = re.sub(r'[\s|]', '', grid)
    vel = {'X': 120, 'x': 100, 'o': 70, 'g': 45}
    hits = []
    for i, c in enumerate(s):
        if c in vel:
            hits.append([i, 1, vel[c]])
        elif c == '-':
            if hits and hits[-1][0] + hits[-1][1] == i:
                hits[-1][1] += 1
        elif c not in '._':
            raise TheoryError(f'bad grid character {c!r} in {grid!r} (use X x o g - .)')
    return [tuple(h) for h in hits]


def grid_steps(grid: str) -> int:
    return len(re.sub(r'[\s|]', '', grid))


def humanize(notes: list, amount: float, ppq: int, seed: int = 0, swing: float = 0.0) -> None:
    """Nudge timing and velocity in place. amount 0-1; swing 0-1 delays off-beat 16ths."""
    rng = random.Random(seed)
    s16 = ppq // 4
    for n in notes:
        if swing and (n.position // s16) % 2 == 1 and n.position % s16 == 0:
            n.position += int(s16 * swing * 0.5)
        if amount:
            n.position = max(0, n.position + int(rng.gauss(0, amount * s16 * 0.12)))
            n.velocity = max(1, min(127, int(n.velocity + rng.gauss(0, amount * 10))))
