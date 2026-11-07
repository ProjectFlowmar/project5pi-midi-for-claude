# Notes on the FL Studio project format (.flp)

What we learned building a writer that FL Studio 2026 accepts, from reading
projects that FL itself saved (FL's own templates and demo songs, versions 20.5
through 26.1). Shared so other tools don't have to rediscover it. Corrections welcome.

## Container

```
"FLhd" u32 len=6   u16 format, u16 channel count, u16 PPQ
"FLdt" u32 len     events...
```

Each event is `id` plus payload. The size comes from the id range:

| id | payload |
|---|---|
| 0-63 | 1 byte |
| 64-127 | 2 bytes |
| 128-191 | 4 bytes |
| 192-255 | varint length (7 bits per byte, little end first) + bytes |

**Exception (new in FL 2025, v25.2+):** event **172** has a **3-byte** payload.
Files from 24.2 and older never contain it. A parser that applies the range rule
to it drifts, and on FL 2026 files typically loses the first channel or fails on
a truncated text event. Gate on the version string (event 199, always first).

FL processes events in order with "current object" state (event 64 selects a
channel, 65 a pattern, 99 an arrangement), so you can insert and replace events
and keep everything else byte-for-byte. This library round-trips all 51 projects
we tested exactly.

## Events used here

| id | meaning |
|---|---|
| 9 | song mode flag. 1 = command-line render plays the playlist; 0 = it renders only the current pattern (one loop) |
| 64 | new channel (u16 id) |
| 65 | new/select pattern (u16 id, 1-based) |
| 99 / 100 | new arrangement / current arrangement index (FL 20+ can hold several; write to the current one) |
| 148 (+33, 34, 205) | time marker position (high bits = marker type), numerator, denominator, name. An end marker stretches renders |
| 156 | tempo, u32 BPM x 1000 |
| 193 | pattern name (UTF-16LE) |
| 194 / 195 / 206 / 207 | title / comment / genre / artist (end up in rendered file tags) |
| 199 | version string (ASCII) |
| 201 / 203 / 196 | plugin internal name / display name / sample path (UTF-16LE) |
| 224 | pattern notes, 24 bytes each |
| 233 | playlist items of the current arrangement |
| 238 / 239 | playlist track data / track name |

Mixer inserts and effect slots reuse event 203 for names; stop reading channel
names at the first mixer event (236 or 98).

## Note (24 bytes)

```
u32 position (ticks)   u16 flags (0x4000)   u16 channel id   u32 length
u16 key (MIDI)         u16 group             u8 fine pitch (120 = 0)  u8 -
u8 release (64)        u8 midi channel       u8 pan (64)      u8 velocity
u8 mod x (128)         u8 mod y (128)
```

## Playlist item: size changes with the version

| FL version | bytes per item |
|---|---|
| up to 20.9 | 32 |
| 20.99 (21 betas) - 24.1 | 60 |
| 24.2 - 2025 (25.x) | 80 |
| 2026 (26.x) | 88 |

Common 32-byte head:

```
u32 position   u16 pattern_base (20480)   u16 item_index (20480 + pattern id for pattern clips)
u32 length     u16 track (500 - track number)   u16 group
u8[2] (120, 0)   u16 flags (64)   u8[4] (64, 100, 128, 128)
start/end offsets: float32 -1.0 each (<= 20.9), int32 -1 each or (0, length) in ticks (newer)
```

From 60 bytes on, a u32 running clip number (1, 2, 3, ...) follows, then 24 zero
bytes. The 80-byte layout appends `00000000 00000000 0000f03f 00000000 00000000`.
The 88-byte layout appends `00000000 00000000 0000f03f 00000000 ffffffff 00000000 00000000`.
Writing the wrong size is not an error to FL: it silently reads the items out of
alignment. Typically the first clip plays and later clips vanish, so check render
length, not just "it opened". When a file already has items, measure the size from
them (every item has 20480 at byte offset 4) before trusting the version table.

## Command-line render

```
FL64.exe /R /Ewav "C:\path\song.flp"
```

Writes `song.wav` next to the project, then exits (about 5-20 s). `/F<path>` did
not load the project in FL 2026. FL must not already be running. Formats: wav, mp3,
ogg, flac (comma separated).
