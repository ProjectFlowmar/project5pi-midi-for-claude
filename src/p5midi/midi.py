"""Export a built song as a Standard MIDI File (type 1), one track per part.

Lets the same spec drive any DAW (Ableton, Logic, Reaper, ...) even without FL Studio.
The whole arrangement is written out, not just the loops. Drum parts go on MIDI channel 10.
"""
from __future__ import annotations

import struct
from pathlib import Path


def _vlq(n: int) -> bytes:
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    return bytes(reversed(out))


def _track(events: list[tuple[int, bytes]]) -> bytes:
    events.sort(key=lambda e: (e[0], e[1][0] & 0xF0 == 0x90))  # note-offs before note-ons at same tick
    body, last = bytearray(), 0
    for t, msg in events:
        body += _vlq(t - last) + msg
        last = t
    body += b'\x00\xff\x2f\x00'
    return b'MTrk' + struct.pack('>I', len(body)) + bytes(body)


def export_midi(result, path: str | Path, drum_parts: set[str] | None = None) -> Path:
    """result: song.BuildResult. Returns the written path."""
    proj = result.project
    ppq = proj.ppq
    by_pattern = {p.pattern_id: p for p in result.parts.values()}
    if drum_parts is None:
        drum_parts = {n for n, p in result.parts.items() if p.kind == 'drums'}
    tempo = int(round(60_000_000 / result.bpm))
    conductor = [(0, b'\xff\x51\x03' + tempo.to_bytes(3, 'big')), (0, b'\xff\x58\x04\x04\x02\x18\x08')]
    tracks = [_track(conductor)]
    melodic = [c for c in range(16) if c != 9]
    mi = 0
    for pname, part in result.parts.items():
        if pname in drum_parts:
            ch = 9
        else:
            ch = melodic[mi % len(melodic)]
            mi += 1
        name = pname.encode('utf-8')[:120]
        ev: list[tuple[int, bytes]] = [(0, b'\xff\x03' + _vlq(len(name)) + name)]
        for clip in proj.clips():
            if by_pattern.get(clip.pattern) is not part:
                continue
            for n in part.notes:
                if n.position >= clip.length:
                    continue
                on = clip.position + n.position
                off = clip.position + min(n.position + n.length, clip.length)
                ev.append((on, bytes([0x90 | ch, n.key & 0x7F, max(1, n.velocity) & 0x7F])))
                ev.append((max(on + 1, off), bytes([0x80 | ch, n.key & 0x7F, 0])))
        tracks.append(_track(ev))
    data = b'MThd' + struct.pack('>IHHH', 6, 1, len(tracks), ppq) + b''.join(tracks)
    path = Path(path)
    path.write_bytes(data)
    return path
