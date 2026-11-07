---
name: fl-studio-composer
description: Compose, render and check a song in FL Studio from a text idea using the p5midi tool. Use when the user asks for a beat, song, loop, track or instrumental made in FL Studio, or wants an idea turned into an .flp / MIDI / MP3.
---

# Compose a song in FL Studio

You write the music as a JSON spec; `p5midi` turns it into a real FL Studio
project, renders it with the user's FL Studio, masters it and measures it.
You cannot hear. Treat the analysis numbers as your ears and ask the user to
listen before calling anything finished.

## Workflow

1. **Pick a template** that matches the genre: `p5midi templates`.
   Good starting points: HipHop-Trap, Trap, Chillout, Chillout-Ambient, EDM-House,
   Drum & Bass, Dubstep, Electro, Breakbeat, Funk, Jazz, Rock, Metal, Basic.
2. **Read its channels**: `p5midi inspect "<template>"`. Use these names in
   each part's `channel`. Drums on FPC take General MIDI lane names (kick, snare,
   hat...); single-sample channels take note `"60"`.
3. **Write the spec**: the format is in `SPEC.md` (or the MCP `spec_format` tool).
   Decide key, tempo, progression, 3-6 parts, and an arrangement with contrast
   (intro, build, main, break, main, outro). Typical lengths: 8-bar sections,
   1-3 minutes total.
4. **Make it**: `p5midi make song.json -o out/song.flp`
   (needs Windows, FL Studio closed, ffmpeg). Produces song.flp, song.mid,
   song.wav and a mastered song.mp3, and prints a JSON report.
5. **Read the report** and fix before showing the user:
   - `analysis.notes` lists concrete problems (clipping, silent section, bass-heavy,
     a thin section louder than a full one).
   - `analysis.sections[].lufs` should rise into the main sections and fall in breaks.
   - `master.warning` means it could not reach the loudness target cleanly.
   Adjust part `velocity`, remove parts from crowded sections, re-run `make`.
6. **Hand over** the MP3 and the .flp (the user can open and tweak every note in
   FL Studio) and ask what to change: energy, darker/brighter, tempo, drum feel.

## Writing good parts

- Drums: vary velocity (X accents, g ghosts), add a hat roll at phrase ends,
  `humanize` 0.2-0.4. Two-bar patterns feel less robotic than one-bar ones.
- Bass follows the chord roots; leave space for the kick (rests on kick steps).
- Chords: use 7ths/9ths for lo-fi, jazz, R&B; plain triads for trap, house, rock.
- Melody: mostly stepwise, repeat a 2-bar motif with a variation on the answer,
  end phrases on chord tones. Small integers in a `melody` part are scale degrees.
- Contrast is what makes it sound produced: drop drums in the break, bring a new
  part in at each section, strip back for the outro.
- With `--reference song.mp3` the report compares tonal balance against a track
  the user likes.

## Do not

- Claim it "sounds great" from numbers alone.
- Overwrite the user's own projects; write new files under an output folder.
- Run `render` while FL Studio is open (close it or ask the user to).
