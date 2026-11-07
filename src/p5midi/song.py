"""Song spec (plain JSON) -> FL Studio project.

A spec names a template (whose instruments, mixer and effects are kept), a key,
a tempo, a set of parts and an arrangement. See docs/SPEC.md for the full format
and examples/ for complete songs.
"""
from __future__ import annotations

import copy
import json
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from . import theory
from .flp import Clip, FLPError, Note, Project

PART_TYPES = ('drums', 'chords', 'bass', 'arp', 'notes', 'melody')


class SpecError(ValueError):
    pass


@dataclass
class BuiltPart:
    name: str
    channel_id: int
    channel_name: str
    bars: int
    notes: list[Note]
    pattern_id: int = 0
    track: int = 0
    kind: str = ''


@dataclass
class BuildResult:
    project: Project
    parts: dict[str, BuiltPart]
    sections: list[dict] = field(default_factory=list)  # name, start_bar, bars, start_s, end_s, parts
    bpm: float = 120.0
    total_bars: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def seconds_per_bar(self) -> float:
        return 4 * 60.0 / self.bpm

    def report(self) -> dict:
        return {
            'bpm': self.bpm, 'bars': self.total_bars,
            'length_seconds': round(self.total_bars * self.seconds_per_bar, 2),
            'parts': {k: {'channel': p.channel_name, 'bars': p.bars, 'notes': len(p.notes), 'track': p.track}
                      for k, p in self.parts.items()},
            'sections': [{k: s[k] for k in ('name', 'start_bar', 'bars', 'parts')} for s in self.sections],
            'warnings': self.warnings,
        }


def load_spec(path_or_text: str | Path | dict) -> dict:
    if isinstance(path_or_text, dict):
        return copy.deepcopy(path_or_text)
    p = Path(str(path_or_text))
    text = p.read_text(encoding='utf-8') if len(str(path_or_text)) < 1024 and p.exists() else str(path_or_text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise SpecError(f'spec is not valid JSON: {e}') from None


def _progression(part: dict, spec: dict) -> list[str]:
    prog = part.get('progression', spec.get('progression'))
    if not prog:
        raise SpecError(f'part needs a "progression" (or set one at song level)')
    return list(prog)


def _bars_per_chord(part: dict, spec: dict) -> float:
    return float(part.get('bars_per_chord', spec.get('bars_per_chord', 1)))


def _loop_grid(grid: str, bars: int, steps_per_bar: int) -> list[tuple[int, int, int]]:
    """Repeat a grid (one or more bars long) to fill `bars` bars."""
    n = theory.grid_steps(grid)
    if n == 0:
        return []
    hits = theory.parse_grid(grid)
    total = bars * steps_per_bar
    out = []
    for rep in range(0, total, n):
        for s, length, vel in hits:
            if rep + s < total:
                out.append((rep + s, length, vel))
    return out


def _part_notes(name: str, part: dict, spec: dict, ppq: int, channel_id: int) -> tuple[list[Note], int]:
    kind = part.get('type')
    if kind not in PART_TYPES:
        raise SpecError(f'part {name!r}: "type" must be one of {", ".join(PART_TYPES)}')
    key = part.get('key', spec.get('key', 'C major'))
    spb = int(part.get('steps_per_bar', 16))
    step = ppq * 4 // spb
    beat = ppq
    vel_scale = float(part.get('velocity', 1.0))
    transpose = int(part.get('transpose', 0))
    notes: list[Note] = []

    def add(pos, key_, length, vel):
        notes.append(Note(int(pos), int(key_) + transpose, max(1, int(length)), channel_id,
                          int(max(1, min(127, vel * vel_scale)))))

    if kind == 'drums':
        lanes = part.get('lanes') or {k: v for k, v in part.items() if isinstance(v, str) and k not in
                                      ('type', 'channel', 'key')}
        if not lanes:
            raise SpecError(f'part {name!r}: drums need "lanes", e.g. {{"kick": "x...x..."}}')
        drum_map = {**theory.GM_DRUMS, **{k: int(v) for k, v in part.get('map', {}).items()}}
        bars = int(part.get('bars', max(1, -(-max(theory.grid_steps(g) for g in lanes.values()) // spb))))
        for lane, grid in lanes.items():
            if lane.isdigit():
                k = int(lane)
            elif lane in drum_map:
                k = drum_map[lane]
            else:
                raise SpecError(f'part {name!r}: unknown drum {lane!r}; use a note number or one of '
                                f'{", ".join(sorted(drum_map))}')
            roll = part.get('rolls', {}).get(lane) if isinstance(part.get('rolls'), dict) else None
            for s, length, vel in _loop_grid(grid, bars, spb):
                add(s * step, k, step * length, vel)
            if roll:  # {"bar": 2, "step": 12, "count": 6, "steps": 4}: ratchet / hat roll
                for r in roll if isinstance(roll, list) else [roll]:
                    start = ((int(r.get('bar', 1)) - 1) * spb + int(r.get('step', 12))) * step
                    span = int(r.get('steps', 4)) * step
                    count = int(r.get('count', 6))
                    for i in range(count):
                        add(start + i * span // count, k, max(1, span // count), 55 + int(50 * i / max(1, count - 1)))
        return notes, bars

    if kind in ('chords', 'bass', 'arp'):
        prog = _progression(part, spec)
        bpc = _bars_per_chord(part, spec)
        octave = int(part.get('octave', {'chords': 4, 'bass': 1, 'arp': 4}[kind]))
        chords = [theory.chord_from_roman(sym, key, octave) for sym in prog]
        if kind == 'chords' and part.get('voicing', 'smooth') == 'smooth':
            chords = theory.voice_lead(chords, center=12 * (octave + 1) + 4)
        chord_len = int(bpc * 4 * beat)
        bars = int(part.get('bars', max(1, round(len(prog) * bpc))))
        rhythm = part.get('rhythm')
        for ci, ch in enumerate(chords):
            start = ci * chord_len
            if start >= bars * 4 * beat:
                break
            if kind == 'chords':
                if rhythm:
                    for s, length, vel in _loop_grid(rhythm, max(1, int(bpc)), spb):
                        if s * step < chord_len:
                            for k in ch:
                                add(start + s * step, k, step * length, vel * 0.85)
                else:
                    for k in ch:
                        add(start, k, chord_len, 85)
                    if part.get('add_bass_note'):
                        add(start, ch[0] - 12, chord_len, 80)
            elif kind == 'bass':
                root = min(ch) % 12 + 12 * (octave + 1)
                grid = rhythm or ('x' + '-' * (spb - 1))
                for s, length, vel in _loop_grid(grid, max(1, int(round(bpc))), spb):
                    if s * step < chord_len:
                        add(start + s * step, root, step * length, vel)
                for extra in part.get('octave_jumps', []):  # [step,...] relative to chord start: up an octave
                    if int(extra) * step < chord_len:
                        add(start + int(extra) * step, root + 12, step * 2, 95)
            else:  # arp
                rate = int(part.get('rate', 16))
                astep = ppq * 4 // rate
                span = int(part.get('span', 1))
                pool = sorted(ch) + [n + 12 * o for o in range(1, span + 1) for n in sorted(ch)]
                pat = part.get('pattern', 'up')
                if pat == 'down':
                    seq = pool[::-1]
                elif pat == 'updown':
                    seq = pool + pool[-2:0:-1]
                elif pat == 'random':
                    import random
                    rng = random.Random(int(part.get('seed', ci)))
                    seq = [rng.choice(pool) for _ in range(16)]
                else:
                    seq = pool
                gate = float(part.get('gate', 0.8))
                for i in range(chord_len // astep):
                    add(start + i * astep, seq[i % len(seq)], astep * gate, 90 if i % 4 == 0 else 75)
        return notes, bars

    # notes / melody: explicit [beat, pitch, length_beats, velocity?]; pitch may be a note name,
    # a MIDI number, or (melody) {"degree": 5, "octave": 5}
    rows = part.get('notes')
    if not rows:
        raise SpecError(f'part {name!r}: needs "notes": [[beat, pitch, length_beats, velocity], ...]')
    octave = int(part.get('octave', 5))
    last_end = 0
    for row in rows:
        if isinstance(row, dict):
            b, p, length, vel = row['beat'], row.get('pitch', row.get('degree')), row.get('length', 1), row.get('velocity', 100)
            if 'degree' in row:
                p = theory.degree_to_note(key, int(row['degree']), int(row.get('octave', octave)))
        else:
            b, p, length, *rest = row
            vel = rest[0] if rest else 100
        if kind == 'melody' and isinstance(p, int) and not isinstance(row, dict) and -14 <= p <= 21:
            p = theory.degree_to_note(key, p, octave)  # small ints in melody = scale degrees
        k = theory.note_number(p)
        add(float(b) * beat, k, float(length) * beat, float(vel))
        last_end = max(last_end, float(b) + float(length))
    bars = int(part.get('bars', max(1, -(-int(last_end * 1000) // 4000))))
    return notes, bars


def build(spec_in, template: str | Path | None = None) -> BuildResult:
    from .fl import find_template  # local import: fl.py touches the filesystem

    spec = load_spec(spec_in)
    tpath = Path(template) if template else find_template(spec.get('template', 'Basic'))
    proj = Project.load(tpath)
    ppq = proj.ppq
    bar = ppq * 4
    warnings: list[str] = []

    if 'bpm' in spec:
        proj.tempo = float(spec['bpm'])
    proj.set_info(title=spec.get('title', ''), artist=spec.get('artist', ''), genre=spec.get('genre', ''),
                  comment=spec.get('comment', 'Made with Project5pi MIDI for Claude'))
    bpm = proj.tempo or 120.0

    if spec.get('keep_template_content'):
        pass
    else:
        proj.clear_patterns()
        proj.clear_playlist()
        proj.clear_track_names()
        proj.clear_markers()

    # kit: add channels before parts look them up
    #   {"name": "Kick", "sample": "D:/Samples/kick.wav", "root": "C5"}   new Sampler channel (root = the sample's pitch)
    #   {"name": "Keys", "from": "Chillout", "channel": "Piano"}          instrument copied from another template
    root_shift: dict[int, int] = {}
    kit = spec.get('kit') or {}
    kit_channels = kit.get('channels', []) if isinstance(kit, dict) else kit
    if kit_channels:
        # always clone a plain Sampler from the donor (Basic's 808 Kick): a template's own samplers can carry
        # reverse, low volume or odd envelopes (HipHop-Trap's "Reverse Crash" made short hits silent)
        donor = Project.load(find_template(kit.get('donor', 'Basic') if isinstance(kit, dict) else 'Basic'))
        others: dict[str, Project] = {}
        for k in kit_channels:
            try:
                if k.get('sample'):
                    cid = proj.add_sampler(k['name'], k['sample'], donor=donor)
                    if k.get('root') is not None:
                        root_shift[cid] = 60 - theory.note_number(k['root'])
                elif k.get('from'):
                    src = others.setdefault(k['from'], Project.load(find_template(k['from'])))
                    proj.copy_channel_from(src, k['channel'], name=k.get('name'))
                else:
                    raise SpecError(f'kit channel {k.get("name")!r} needs "sample" or "from" + "channel"')
            except FLPError as e:
                raise SpecError(f'kit channel {k.get("name")!r}: {e}') from None

    parts_spec = spec.get('parts') or {}
    if not parts_spec:
        raise SpecError('spec needs "parts"')
    built: dict[str, BuiltPart] = {}
    for name, part in parts_spec.items():
        if 'channel' not in part:
            raise SpecError(f'part {name!r} needs "channel" (a channel name from `p5midi inspect`)')
        try:
            ch = proj.find_channel(part['channel'])
        except FLPError as e:
            raise SpecError(f'part {name!r}: {e}') from None
        if ch.id in root_shift:  # a sample recorded at another pitch: shift so notes sound where written
            part = {**part, 'transpose': int(part.get('transpose', 0)) + root_shift[ch.id]}
        notes, bars = _part_notes(name, part, spec, ppq, ch.id)
        if part.get('humanize') or part.get('swing', spec.get('swing')):
            theory.humanize(notes, float(part.get('humanize', 0)), ppq, seed=zlib.crc32(name.encode()),
                            swing=float(part.get('swing', spec.get('swing', 0))))
        if not notes:
            warnings.append(f'part {name!r} produced no notes')
        built[name] = BuiltPart(name, ch.id, ch.label, bars, notes, kind=part['type'])

    # one pattern per part, one playlist track per part (in order of first use)
    for bp in built.values():
        bp.pattern_id = proj.add_pattern(bp.notes, name=bp.name)

    arrangement = spec.get('arrangement') or [{'name': 'loop', 'bars': max(p.bars for p in built.values()),
                                              'parts': list(built)}]
    clips: list[Clip] = []
    sections = []
    cursor = 0
    next_track = 1
    spb_s = 4 * 60.0 / bpm
    for sec in arrangement:
        bars = int(sec.get('bars', 4))
        names = sec.get('parts', [])
        for pn in names:
            if pn not in built:
                raise SpecError(f'section {sec.get("name", "?")!r} uses unknown part {pn!r}')
            bp = built[pn]
            if not bp.track:
                bp.track = next_track
                next_track += 1
            pos = 0
            while pos < bars:
                length = min(bp.bars, bars - pos)
                clips.append(Clip(bp.pattern_id, bp.track, (cursor + pos) * bar, length * bar))
                pos += bp.bars
        sections.append({'name': sec.get('name', f'section {len(sections) + 1}'), 'start_bar': cursor + 1,
                         'bars': bars, 'parts': names,
                         'start_s': round(cursor * spb_s, 3), 'end_s': round((cursor + bars) * spb_s, 3)})
        cursor += bars
    for bp in built.values():
        if not bp.track:
            warnings.append(f'part {bp.name!r} is never used in the arrangement')
        else:
            proj.set_track_name(bp.track, bp.name)
    proj.set_clips(clips)
    proj.set_song_mode(True)
    return BuildResult(proj, built, sections, bpm, cursor, warnings)
