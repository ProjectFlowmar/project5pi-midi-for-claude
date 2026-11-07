"""Idea -> song spec, with any OpenAI-compatible model (DeepSeek, OpenAI, OpenRouter, a local Ollama).

    spec = compose("dark trap, 140, sliding 808", ai=AI.from_env())
    spec = revise(spec, "more energy in the hook", ai=...)

The model gets the spec format, the channels of the templates installed on this
machine, and musical rules; it must answer with one JSON spec. Every answer is
test-built; a broken one goes back to the model with the error (twice at most).
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .song import SpecError, build

# Templates worth offering the model, in rough genre order. Only the ones installed are sent.
CURATED = ['HipHop-Trap', 'Trap', 'Chillout', 'Chillout-Ambient', 'EDM-House', 'Drum & Bass', 'Dubstep',
           'Electro', 'Breakbeat', 'Funk', 'Jazz', 'Rock', 'Metal', 'Create a chord progression', 'Basic']

RULES = """You are a music producer writing songs for FL Studio as JSON.
Answer with ONE JSON object that follows the spec format below. No prose, no code fences.

Hard rules:
- "template" must be one of the templates listed below, and every part's "channel" must be a channel
  name listed under THAT template, spelled exactly.
- Use 3 to 7 parts. Give each part a short lowercase name.
- Keep the total of all section "bars" at or under MAX_BARS.
- Drum grids: 16 steps per bar (spaces allowed between bars). On FPC drum channels use lane names
  (kick, snare, clap, hat, ohh, crash, ride, rim, perc...). On single-sound channels (a kick, a snare,
  a hat channel) use the lane "60".
- Melody notes: [beat, pitch, length_in_beats, velocity?]; beats start at 0, 4 beats per bar; pitch as a
  note name like "E5" (C4 = middle C) or, in "melody" parts, a scale degree number.

Make it sound produced:
- Contrast: intro thinner than the main sections, drop drums in a break, bring a part in at each new
  section, strip back for the outro. Typical: 4-8 bar intro, 8-bar main sections.
- Drums: accents (X), ghost notes (g), a hat roll or fill at phrase ends, humanize 0.2-0.4. Two-bar
  drum patterns feel less robotic than one-bar ones.
- Bass follows the chord roots and leaves room for the kick. 808s: long notes with a few re-hits.
- Chords: 7ths/9ths for lo-fi, jazz, R&B, chill; triads for trap, house, rock, metal.
- Melody: a 2-bar motif, then a varied answer; mostly stepwise; land on chord tones; 4-bar phrases.
- Balance: pads and arps quieter ("velocity" 0.5-0.7) than drums, bass and the lead.
- Pick a tempo and key that fit the genre unless the idea says otherwise.
"""


class ComposeError(RuntimeError):
    pass


@dataclass
class AI:
    """An OpenAI-compatible chat endpoint."""
    url: str = 'https://api.deepseek.com/v1/chat/completions'
    key: str = ''
    model: str = 'deepseek-v4-flash'
    timeout: int = 180

    @classmethod
    def from_env(cls) -> 'AI':
        """P5MIDI_AI_URL / P5MIDI_AI_KEY / P5MIDI_AI_MODEL, or DEEPSEEK_API_KEY / OPENAI_API_KEY."""
        e = lambda k, d='': os.environ.get(k, d).strip()  # noqa: E731  (keys saved by Notepad can end in a carriage return)
        if e('P5MIDI_AI_KEY') or e('P5MIDI_AI_URL'):
            return cls(e('P5MIDI_AI_URL', cls.url), e('P5MIDI_AI_KEY'), e('P5MIDI_AI_MODEL', cls.model))
        if e('DEEPSEEK_API_KEY'):
            return cls(key=e('DEEPSEEK_API_KEY'), model=e('P5MIDI_AI_MODEL', cls.model))
        if e('OPENAI_API_KEY'):
            return cls('https://api.openai.com/v1/chat/completions', e('OPENAI_API_KEY'), e('P5MIDI_AI_MODEL', 'gpt-4.1'))
        raise ComposeError('No AI configured. Set DEEPSEEK_API_KEY (or P5MIDI_AI_URL + P5MIDI_AI_KEY + '
                           'P5MIDI_AI_MODEL for any OpenAI-compatible endpoint).')

    def chat(self, messages: list[dict], think: bool = True) -> tuple[str, dict]:
        body = {'model': self.model, 'messages': messages, 'temperature': 0.8, 'max_tokens': 8000,
                'response_format': {'type': 'json_object'}}
        if not think and 'deepseek' in self.url:  # v4 models can spend the whole budget reasoning; planning doesn't need it
            body['thinking'] = {'type': 'disabled'}
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), method='POST', headers={
            'content-type': 'application/json', 'authorization': f'Bearer {self.key}'})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise ComposeError(f'AI request failed: HTTP {e.code} {e.read()[:300]!r}') from None
        except (urllib.error.URLError, TimeoutError) as e:
            raise ComposeError(f'AI request failed: {e}') from None
        return data['choices'][0]['message'].get('content') or '', data.get('usage', {})


def template_catalog(names: list[str] | None = None) -> dict[str, list[dict]]:
    """{template: [{channel, plugin}]} for the curated templates installed here."""
    from .fl import list_templates
    from .flp import Project
    have = {t['name']: t['path'] for t in list_templates()}
    out = {}
    for n in names or CURATED:
        if n in have:
            try:
                chans = Project.load(have[n]).channels()
            except Exception:
                continue
            out[n] = [{'channel': c.label, 'plugin': c.plugin or Path(c.sample).name} for c in chans]
    if not out:
        raise ComposeError('No FL Studio templates found. Install FL Studio or set P5MIDI_TEMPLATES.')
    return out


def _spec_doc() -> str:
    p = Path(__file__).with_name('SPEC.md')
    return p.read_text(encoding='utf-8') if p.exists() else ''


def parse_json(text: str) -> dict:
    t = text.strip()
    t = re.sub(r'^```(?:json)?\s*|\s*```$', '', t)
    if not t:
        raise ComposeError('the AI returned an empty answer')
    i, j = t.find('{'), t.rfind('}')
    try:
        return json.loads(t[i:j + 1] if i >= 0 else t)
    except json.JSONDecodeError as e:
        raise ComposeError(f'the AI answer was not valid JSON ({e})') from None


def total_bars(spec: dict) -> int:
    return sum(int(s.get('bars', 4)) for s in spec.get('arrangement') or [])


def enforce_length(spec: dict, max_seconds: float | None) -> dict:
    """Trim the arrangement so the song fits max_seconds (keeps whole sections, cuts the last one short)."""
    if not max_seconds:
        return spec
    bpm = float(spec.get('bpm', 120))
    max_bars = max(4, int(max_seconds * bpm / 240))
    kept, used = [], 0
    for s in spec.get('arrangement') or []:
        if used >= max_bars:
            break
        b = min(int(s.get('bars', 4)), max_bars - used)
        kept.append({**s, 'bars': b})
        used += b
    spec['arrangement'] = kept
    return spec


def _system(catalog: dict, max_bars: int) -> str:
    return (RULES.replace('MAX_BARS', str(max_bars)) + '\n## Templates and their channels\n' +
            json.dumps(catalog, indent=1) + '\n\n## Spec format\n' + _spec_doc())


def catalog_check(catalog: dict):
    """A validator for machines without the templates (the hosted service): names must match the catalog."""
    def check(spec: dict) -> None:
        tpl = spec.get('template')
        if tpl not in catalog:
            raise SpecError(f'"template" must be one of: {", ".join(catalog)}')
        names = {c['channel'] for c in catalog[tpl]}
        parts = spec.get('parts') or {}
        if not parts:
            raise SpecError('spec needs "parts"')
        from .song import PART_TYPES
        for n, p in parts.items():
            if p.get('type') not in PART_TYPES:
                raise SpecError(f'part {n!r}: "type" must be one of {", ".join(PART_TYPES)}')
            if p.get('channel') not in names:
                raise SpecError(f'part {n!r}: no channel named {p.get("channel")!r} in {tpl}. '
                                f'Channels: {", ".join(sorted(names))}')
        for sec in spec.get('arrangement') or []:
            for pn in sec.get('parts', []):
                if pn not in parts:
                    raise SpecError(f'section {sec.get("name", "?")!r} uses unknown part {pn!r}')
    return check


def _validated(ai: AI, messages: list[dict], max_seconds, tries: int = 3, validate=None) -> tuple[dict, dict]:
    usage_total = {'prompt_tokens': 0, 'completion_tokens': 0}
    last_err = None
    for _ in range(tries):
        text, usage = ai.chat(messages)
        for k in usage_total:
            usage_total[k] += int(usage.get(k, 0) or 0)
        try:
            spec = enforce_length(parse_json(text), max_seconds)
            (validate or build)(spec)  # raises SpecError on a bad channel, part, grid...
            return spec, usage_total
        except (ComposeError, SpecError, ValueError, KeyError, TypeError) as e:
            last_err = e
            # keep history as JSON (DeepSeek returns blanks after prose history in json mode)
            messages = messages + [{'role': 'assistant', 'content': text.strip() or '{}'},
                                   {'role': 'user', 'content': json.dumps({'error': str(e)[:600],
                                    'fix': 'Return the whole corrected JSON spec.'})}]
    raise ComposeError(f'could not get a valid song from the AI after {tries} tries: {last_err}')


def compose(idea: str, ai: AI | None = None, max_seconds: float | None = None,
            catalog: dict | None = None, validate=None) -> tuple[dict, dict]:
    """Returns (spec, token usage)."""
    ai = ai or AI.from_env()
    catalog = catalog or template_catalog()
    max_bars = int((max_seconds or 240) * 140 / 240) if max_seconds else 96
    messages = [{'role': 'system', 'content': _system(catalog, max_bars)},
                {'role': 'user', 'content': json.dumps({'idea': idea.strip()[:2000],
                 'length': f'about {int(max_seconds)} seconds' if max_seconds else '1.5 to 3 minutes'})}]
    return _validated(ai, messages, max_seconds, validate=validate)


def revise(spec: dict, instruction: str, ai: AI | None = None, max_seconds: float | None = None,
           catalog: dict | None = None, analysis_notes: list[str] | None = None,
           validate=None) -> tuple[dict, dict]:
    """Change an existing song: 'more energy', 'darker', 'simpler drums', or mix notes from analyze()."""
    ai = ai or AI.from_env()
    catalog = catalog or template_catalog([spec.get('template', 'Basic')] + CURATED)
    max_bars = total_bars(spec) if max_seconds else max(total_bars(spec), 96)
    ask = {'current_song': spec, 'change': instruction.strip()[:1000],
           'keep': 'Keep the template, key and anything the change does not ask about. Return the whole JSON spec.'}
    if analysis_notes:
        ask['mix_problems'] = analysis_notes
        ask['how_to_fix_mix'] = 'Adjust part "velocity", move parts between sections, or simplify parts.'
    messages = [{'role': 'system', 'content': _system(catalog, max_bars)},
                {'role': 'user', 'content': json.dumps(ask)}]
    return _validated(ai, messages, max_seconds, validate=validate)


def fix(spec: dict, error: str, ai: AI | None = None, max_seconds: float | None = None,
        catalog: dict | None = None, validate=None) -> tuple[dict, dict]:
    """Repair a spec that failed to build on the user's machine (hosted flow)."""
    return revise(spec, f'This spec failed to build with this error: {error[:600]}. Fix only that.',
                  ai=ai, max_seconds=max_seconds, catalog=catalog, validate=validate)
