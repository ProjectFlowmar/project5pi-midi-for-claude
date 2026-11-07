# Song spec format

A song is one JSON object. The template supplies the sound (instruments, mixer,
effects); the spec supplies the music (notes and arrangement).

```json
{
  "title": "Night Drive",
  "template": "HipHop-Trap",
  "bpm": 140,
  "key": "A minor",
  "progression": ["i", "VI", "III", "VII"],
  "bars_per_chord": 1,
  "swing": 0,
  "parts": { "name": { "type": "...", "channel": "...", ... } },
  "arrangement": [ { "name": "intro", "bars": 4, "parts": ["chords"] } ]
}
```

| Field | Meaning |
|---|---|
| `template` | FL Studio template name (`p5midi templates`) or a path to any `.flp`. Default `Basic`. |
| `bpm` | Tempo. Leave out to keep the template's tempo. |
| `key` | `"A minor"`, `"C major"`, `"F# dorian"`, `"Ebm"`. Modes: major, minor, harmonic_minor, melodic_minor, dorian, phrygian, lydian, mixolydian, locrian. |
| `progression` | Default chord progression (roman numerals) for chords / bass / arp parts. |
| `bars_per_chord` | Default 1. Use 2 for slower harmony, 0.5 for two chords per bar. |
| `swing` | 0-1, delays off-beat 16ths. Can also be set per part. |
| `title`, `artist`, `genre`, `comment` | Written into the project and the rendered file's tags. |
| `keep_template_content` | `true` keeps the template's own patterns and playlist (default clears them). |

## Parts

Every part needs `type` and `channel`. `channel` is a channel name from
`p5midi inspect <template>` (exact, plugin name, or partial match) or a
channel id number as a string. Each part becomes one pattern and one playlist
track. Optional on every part: `velocity` (multiplier, e.g. 0.6), `transpose`
(semitones), `humanize` (0-1, timing and velocity drift, deterministic), `swing`,
`bars` (loop length).

### `drums`: step grids

```json
"drums": {
  "type": "drums", "channel": "Drums", "bars": 2,
  "lanes": {
    "kick":  "x......x..x..... x......x..x...x.",
    "clap":  "........x....... ........x.......",
    "hat":   "x.x.x.x.x.x.x.x. x.x.x.x.x.x....."
  },
  "rolls": {"hat": {"bar": 2, "step": 12, "steps": 4, "count": 6}}
}
```

Grid characters, 16 per bar by default (`steps_per_bar` to change):
`X` accent, `x` hit, `o` soft, `g` ghost, `-` hold previous note, `.` rest.
Spaces and `|` are ignored. A grid shorter than the part loops.

Lane names follow General MIDI (what FPC and most drum plugins use): kick, kick2,
snare, snare2, clap, rim, hat/chh, hat_pedal, ohh/open_hat, crash, crash2, ride,
ride_bell, china, splash, tamb, cowbell, shaker, perc, tom_low, tom_mid, tom_high.
A lane can also be a note number (`"60"`), which is what one-shot samplers want.
Override with `"map": {"kick": 48}`.

`rolls` adds a ratchet: `count` hits spread over `steps` steps starting at `step`
of `bar`, with rising velocity. Use a list for several.

### `chords`

```json
"chords": {"type": "chords", "channel": "Pad", "octave": 4,
           "progression": ["i7", "VImaj7", "III", "VII"], "rhythm": "x..x..x.x..x..x."}
```

Roman numerals take their quality from the key, so `VI` and `vi` are the same
chord in A minor. Suffixes: `7`, `maj7`, `9`, `add9`, `11`, `sus2`, `sus4`,
`dim`, `maj`/`min` (force the third). Prefix `b` or `#` for borrowed roots
(`bVII`, `bVI`); then upper case = major, lower case = minor.
Chords are voice-led (closest inversion to the previous chord) unless
`"voicing": "root"`. Without `rhythm`, each chord is held for its full length.
`"add_bass_note": true` doubles the root an octave down.

### `bass`

```json
"808": {"type": "bass", "channel": "Sub 1", "octave": 1, "rhythm": "x-----.x--.x----", "octave_jumps": [14]}
```

Plays each chord's root on the `rhythm` grid (default: one held note per chord).
`octave_jumps` are steps where the root jumps up an octave.

### `arp`

```json
"keys": {"type": "arp", "channel": "Synth 2", "rate": 16, "pattern": "updown", "span": 1, "gate": 0.8}
```

`rate`: 8 (eighths), 16 (sixteenths), 32. `pattern`: up, down, updown, random
(`seed` to vary). `span`: extra octaves. `gate`: note length as a fraction of a step.

### `melody` / `notes`: explicit notes

```json
"hook": {"type": "melody", "channel": "Synth 1", "octave": 5,
         "notes": [[0, "E5", 0.75], [0.75, "D5", 0.25, 90], [1, 3, 1]]}
```

Each row is `[beat, pitch, length_in_beats, velocity?]`. Beats count from 0 at
the start of the part (4 beats per bar). Pitch is a note name (`C4` = middle C =
MIDI 60; FL's piano roll labels it C5), a MIDI number, or, in `melody` parts
only, a small integer = scale degree in `key` at `octave` (1 = tonic, 8 = tonic
an octave up, 0 and negatives go down). Rows can also be objects:
`{"beat": 0, "degree": 5, "length": 1, "velocity": 100}`.

## Arrangement

```json
"arrangement": [
  {"name": "intro",  "bars": 4, "parts": ["chords", "keys"]},
  {"name": "hook 1", "bars": 8, "parts": ["drums", "808", "chords", "hook"]}
]
```

Sections play in order. Each listed part loops (as whole clips) to fill the
section. Without an arrangement, all parts play once together.

## Tips for AI composers

- Run `inspect` first; channel names differ per template.
- You cannot hear. After `make`, read `analysis.notes` and the per-section LUFS:
  a section with fewer parts should be quieter than a fuller one; if not, lower
  that part's `velocity`.
- Use `--reference some-song.mp3` to compare tonal balance against a track the
  user likes.
- Keep velocities varied (`humanize` 0.2-0.4 on drums and melodies).
- Ask the user to listen before calling a song done.
