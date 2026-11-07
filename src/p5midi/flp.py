"""Read and write FL Studio project files (.flp) at the event level.

An .flp file is a short header chunk ("FLhd") followed by a data chunk ("FLdt")
holding a flat list of events. Each event is one id byte plus a payload:

    id   0-63   -> 1 byte payload
    id  64-127  -> 2 byte payload
    id 128-191  -> 4 byte payload
    id 192-255  -> varint length + that many bytes

FL Studio processes events in order and keeps state (the "current" channel,
pattern, arrangement), so this module edits the list in place and keeps every
event it does not understand exactly as it was. A read followed by a write is
byte-identical.

Event ids used here (names follow the community pyflp project where known):
    9    song mode flag ("loop active" in older docs). 1 = play the playlist.
    64   new channel (u16 channel id)
    65   new pattern / select pattern (u16 pattern id, 1-based)
    99   new arrangement
    156  tempo (u32, BPM * 1000)
    193  pattern name (UTF-16LE, null terminated)
    199  FL version string (ASCII)
    201  plugin internal name (UTF-16LE)
    203  channel / plugin display name (UTF-16LE)
    196  sample path (UTF-16LE)
    224  pattern notes (24 bytes per note)
    233  playlist items (32, 60 or 88 bytes per item depending on FL version)
    238  playlist track data, 239 playlist track name (UTF-16LE)
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

EV_SONG_MODE = 9
EV_CHANNEL_NEW = 64
EV_PATTERN_NEW = 65
EV_ARRANGEMENT_NEW = 99
EV_ARRANGEMENT_CURRENT = 100
EV_MARKER_POS = 148
EV_MARKER_NAME = 205
EV_MARKER_NUM = 33
EV_MARKER_DEN = 34
EV_TEMPO = 156
EV_PATTERN_NAME = 193
EV_TITLE = 194
EV_COMMENT = 195
EV_GENRE = 206
EV_ARTISTS = 207
EV_SAMPLE_PATH = 196
EV_VERSION = 199
EV_PLUGIN_INTERNAL = 201
EV_CHANNEL_NAME = 203
EV_NOTES = 224
EV_UNKNOWN_226 = 226
EV_PLAYLIST = 233
EV_TRACK_DATA = 238
EV_TRACK_NAME = 239
EV_MIXER_SLOT = 98
EV_ROUTED_TO = 143  # channel -> mixer insert (0 = master)
EV_INSERT_FLAGS = 236

PATTERN_BASE = 20480  # playlist item_index = PATTERN_BASE + pattern id

NOTE_STRUCT = struct.Struct('<IHHIHHBBBBBBBB')  # 24 bytes
ITEM_HEAD = struct.Struct('<IHHIHH2sH4s')       # 24 bytes, then 2 offsets = 32
_FL24_TAIL = bytes.fromhex('00000000000000000000f03f0000000000000000')  # 20 bytes (FL 24.2-25)
_FL26_TAIL = bytes.fromhex('00000000000000000000f03f00000000ffffffff0000000000000000')  # 28 bytes (FL 2026)


class FLPError(Exception):
    pass


@dataclass
class Note:
    """One piano-roll note. Times are in ticks at the project's PPQ."""
    position: int
    key: int            # MIDI note number, 60 = middle C (FL shows it as C5)
    length: int
    channel: int        # channel id (from Project.channels)
    velocity: int = 100  # 0-127
    pan: int = 64        # 0-127, 64 = centre
    fine_pitch: int = 120  # 0-240, 120 = no detune
    release: int = 64

    def pack(self) -> bytes:
        return NOTE_STRUCT.pack(
            max(0, int(self.position)), 0x4000, int(self.channel), max(0, int(self.length)),
            int(self.key), 0, int(self.fine_pitch), 0, int(self.release), 0,
            int(self.pan), max(0, min(127, int(self.velocity))), 128, 128)

    @classmethod
    def unpack(cls, b: bytes) -> "Note":
        pos, _flags, ch, length, key, _grp, fine, _u, rel, _mch, pan, vel, _mx, _my = NOTE_STRUCT.unpack(b)
        return cls(pos, key, length, ch, vel, pan, fine, rel)


@dataclass
class Clip:
    """A pattern clip on the playlist. Times are in ticks."""
    pattern: int
    track: int      # 1-based playlist track
    position: int
    length: int


@dataclass
class Channel:
    id: int
    name: str = ''
    plugin: str = ''
    sample: str = ''

    @property
    def label(self) -> str:
        return self.name or self.plugin or Path(self.sample).stem or f'Channel {self.id}'


def _utf16(b: bytes) -> str:
    return b.decode('utf-16-le', 'replace').rstrip('\0')


def _to_utf16(s: str) -> bytes:
    return (s + '\0').encode('utf-16-le')


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        x = n & 0x7F
        n >>= 7
        out.append(x | (0x80 if n else 0))
        if not n:
            return bytes(out)


def parse_events(data: bytes) -> tuple[bytes, list[list]]:
    if data[:4] != b'FLhd':
        raise FLPError('not an FL Studio project (missing FLhd header)')
    hl = struct.unpack('<I', data[4:8])[0]
    header = data[8:8 + hl]
    o = 8 + hl
    if data[o:o + 4] != b'FLdt':
        raise FLPError('missing FLdt data chunk')
    o += 8
    events: list[list] = []
    n = len(data)
    fl25 = False  # FL 2025+ (v25.2, v26.x) writes event 172 with a 3-byte payload
    while o < n:
        eid = data[o]
        o += 1
        if eid == 172 and fl25:
            size = 3
        elif eid < 64:
            size = 1
        elif eid < 128:
            size = 2
        elif eid < 192:
            size = 4
        else:
            size = shift = 0
            while True:
                x = data[o]
                o += 1
                size |= (x & 0x7F) << shift
                shift += 7
                if not x & 0x80:
                    break
        if o + size > n:
            raise FLPError(f'truncated event {eid} at byte {o}')
        events.append([eid, data[o:o + size]])
        o += size
        if eid == EV_VERSION:
            try:
                fl25 = int(data[o - size:o].split(b'.')[0]) >= 25
            except ValueError:
                pass
    return header, events


def serialize_events(header: bytes, events: list[list]) -> bytes:
    body = bytearray()
    for eid, payload in events:
        body.append(eid)
        if eid >= 192:
            body += _varint(len(payload))
        body += payload
    return (b'FLhd' + struct.pack('<I', len(header)) + header +
            b'FLdt' + struct.pack('<I', len(body)) + bytes(body))


class Project:
    """An FL Studio project held as an editable event list."""

    def __init__(self, header: bytes, events: list[list]):
        self.header = header
        self.events = events

    # ---------- load / save ----------
    @classmethod
    def load(cls, path: str | Path) -> "Project":
        return cls(*parse_events(Path(path).read_bytes()))

    def to_bytes(self) -> bytes:
        return serialize_events(self.header, self.events)

    def save(self, path: str | Path) -> None:
        Path(path).write_bytes(self.to_bytes())

    # ---------- basic properties ----------
    @property
    def ppq(self) -> int:
        return struct.unpack('<H', self.header[4:6])[0]

    @property
    def version(self) -> str:
        for eid, d in self.events:
            if eid == EV_VERSION:
                return d.decode('ascii', 'replace').rstrip('\0')
        return '0'

    @property
    def major_version(self) -> int:
        try:
            return int(self.version.split('.')[0])
        except ValueError:
            return 0

    @property
    def tempo(self) -> float | None:
        for eid, d in self.events:
            if eid == EV_TEMPO:
                return struct.unpack('<I', d)[0] / 1000
        return None

    @tempo.setter
    def tempo(self, bpm: float) -> None:
        if not 10 <= bpm <= 522:
            raise FLPError('tempo must be between 10 and 522 BPM')
        for e in self.events:
            if e[0] == EV_TEMPO:
                e[1] = struct.pack('<I', round(bpm * 1000))
                return
        raise FLPError('this project has no tempo event (FL 3.4+ projects only)')

    def set_info(self, title: str | None = None, artist: str | None = None, genre: str | None = None,
                 comment: str | None = None) -> None:
        """Song info FL writes into rendered files. None = leave as is, '' = clear."""
        for eid, val in ((EV_TITLE, title), (EV_ARTISTS, artist), (EV_GENRE, genre), (EV_COMMENT, comment)):
            if val is None:
                continue
            for e in self.events:
                if e[0] == eid:
                    e[1] = _to_utf16(val)
                    break
            else:
                vi = next((i for i, e in enumerate(self.events) if e[0] == EV_VERSION), 0)
                self.events.insert(vi + 1, [eid, _to_utf16(val)])

    def set_song_mode(self, on: bool = True) -> None:
        """Song mode makes a command-line render play the playlist, not one pattern."""
        for e in self.events:
            if e[0] == EV_SONG_MODE:
                e[1] = b'\x01' if on else b'\x00'
                return
        self.events.insert(1, [EV_SONG_MODE, b'\x01' if on else b'\x00'])

    # ---------- channels ----------
    def channels(self) -> list[Channel]:
        """Instrument channels in the channel rack, in order."""
        out: list[Channel] = []
        cur: Channel | None = None
        for eid, d in self.events:
            if eid == EV_CHANNEL_NEW:
                cur = Channel(struct.unpack('<H', d)[0])
                out.append(cur)
            elif eid in (EV_MIXER_SLOT, EV_INSERT_FLAGS, EV_ARRANGEMENT_NEW):
                cur = None  # mixer and playlist sections reuse name events
            elif cur is not None:
                if eid == EV_CHANNEL_NAME and not cur.name:
                    cur.name = _utf16(d)
                elif eid == EV_PLUGIN_INTERNAL and not cur.plugin:
                    cur.plugin = _utf16(d)
                elif eid == EV_SAMPLE_PATH and not cur.sample:
                    cur.sample = _utf16(d)
        return out

    # ---------- channel blocks (kits) ----------
    def _channel_section_end(self) -> int:
        return next((i for i, e in enumerate(self.events) if e[0] in (EV_MIXER_SLOT, EV_INSERT_FLAGS, EV_ARRANGEMENT_NEW)),
                    len(self.events))

    def channel_blocks(self) -> list[tuple[int, list[list]]]:
        """[(channel id, events)] for every channel; pattern events interleaved by FL are left out."""
        end = self._channel_section_end()
        starts = [i for i, e in enumerate(self.events[:end]) if e[0] == EV_CHANNEL_NEW]
        out = []
        for n, a in enumerate(starts):
            b = starts[n + 1] if n + 1 < len(starts) else end
            block = [list(e) for e in self.events[a:b] if e[0] not in (EV_PATTERN_NEW, EV_PATTERN_NAME, EV_NOTES)]
            out.append((struct.unpack('<H', self.events[a][1])[0], block))
        return out

    def _set_channel_count(self) -> None:
        n = sum(1 for e in self.events if e[0] == EV_CHANNEL_NEW)
        self.header = self.header[:2] + struct.pack('<H', n) + self.header[4:]

    def add_channel_block(self, block: list[list], name: str | None = None, sample: str | None = None,
                          route: int | None = 0) -> int:
        """Append a copy of a channel's events as a new channel. Returns the new channel id."""
        new_id = max([cid for cid, _ in self.channel_blocks()] or [-1]) + 1
        blk = [list(e) for e in block]
        blk[0] = [EV_CHANNEL_NEW, struct.pack('<H', new_id)]
        for e in blk:
            if name is not None and e[0] == EV_CHANNEL_NAME:
                e[1] = _to_utf16(name)
            elif sample is not None and e[0] == EV_SAMPLE_PATH:
                e[1] = _to_utf16(str(sample).replace('/', '\\'))
            elif route is not None and e[0] == EV_ROUTED_TO:
                e[1] = struct.pack('<i', route)
        if name is not None and not any(e[0] == EV_CHANNEL_NAME for e in blk):
            blk.insert(1, [EV_CHANNEL_NAME, _to_utf16(name)])
        i = self._channel_section_end()
        self.events[i:i] = blk
        self._set_channel_count()
        return new_id

    def sampler_block(self) -> list[list] | None:
        """A plain Sampler channel to clone (has a sample path, no plugin)."""
        for _, blk in self.channel_blocks():
            plugin = next((e[1] for e in blk if e[0] == EV_PLUGIN_INTERNAL), b'')
            if any(e[0] == EV_SAMPLE_PATH for e in blk) and _utf16(plugin) == '':
                return blk
        return None

    def add_sampler(self, name: str, sample: str | Path, donor: 'Project | None' = None, route: int = 0) -> int:
        """New channel that plays one audio file (pitched by key, like FL's Sampler)."""
        blk = (donor.sampler_block() if donor else None) or self.sampler_block()
        if blk is None:
            raise FLPError('no Sampler channel to copy; pass a donor project such as the Basic template')
        if not Path(sample).is_file():
            raise FLPError(f'sample not found: {sample}')
        return self.add_channel_block(blk, name=name, sample=str(Path(sample).resolve()), route=route)

    def copy_channel_from(self, other: 'Project', channel: str, name: str | None = None, route: int = 0) -> int:
        """Copy an instrument channel (plugin + its settings) from another project."""
        ch = other.find_channel(channel)
        blk = next(b for cid, b in other.channel_blocks() if cid == ch.id)
        return self.add_channel_block(blk, name=name or ch.label, route=route)

    def find_channel(self, name: str | int) -> Channel:
        chans = self.channels()
        if isinstance(name, int) or (isinstance(name, str) and name.isdigit()):
            for c in chans:
                if c.id == int(name):
                    return c
            raise FLPError(f'no channel with id {name}')
        low = name.lower()
        for c in chans:  # exact name first, then plugin, then partial
            if c.label.lower() == low:
                return c
        for c in chans:
            if c.plugin.lower() == low:
                return c
        for c in chans:
            if low in c.label.lower():
                return c
        names = ', '.join(repr(c.label) for c in chans)
        raise FLPError(f'no channel named {name!r}. Channels: {names}')

    # ---------- patterns ----------
    def pattern_ids(self) -> list[int]:
        return sorted({struct.unpack('<H', d)[0] for eid, d in self.events if eid == EV_PATTERN_NEW})

    def patterns(self) -> dict[int, list[Note]]:
        out: dict[int, list[Note]] = {}
        cur = None
        for eid, d in self.events:
            if eid == EV_PATTERN_NEW:
                cur = struct.unpack('<H', d)[0]
            elif eid == EV_NOTES and cur is not None:
                out.setdefault(cur, []).extend(
                    Note.unpack(d[i:i + 24]) for i in range(0, len(d) - len(d) % 24, 24))
        return out

    def pattern_names(self) -> dict[int, str]:
        out, cur = {}, None
        for eid, d in self.events:
            if eid == EV_PATTERN_NEW:
                cur = struct.unpack('<H', d)[0]
            elif eid == EV_PATTERN_NAME and cur is not None:
                out[cur] = _utf16(d)
        return out

    def clear_patterns(self) -> None:
        """Remove all notes from all patterns (pattern headers stay, now empty)."""
        self.events = [e for e in self.events if e[0] != EV_NOTES]

    def _pattern_insert_index(self) -> int:
        first_chan = next((i for i, e in enumerate(self.events) if e[0] == EV_CHANNEL_NEW), None)
        if first_chan is None:
            raise FLPError('project has no channels; start from a template that has instruments')
        last_pat = None
        for i, e in enumerate(self.events[:first_chan]):
            if e[0] in (EV_PATTERN_NEW, EV_NOTES, EV_PATTERN_NAME):
                last_pat = i
        if last_pat is not None:
            return last_pat + 1
        for i, e in enumerate(self.events[:first_chan]):
            if e[0] == EV_UNKNOWN_226:
                return i
        return first_chan

    def add_pattern(self, notes: list[Note], name: str = '') -> int:
        """Add a new pattern holding these notes. Returns its pattern id."""
        pid = max(self.pattern_ids() or [0]) + 1
        block = [[EV_PATTERN_NEW, struct.pack('<H', pid)]]
        if name:
            block.append([EV_PATTERN_NAME, _to_utf16(name)])
        notes = sorted(notes, key=lambda n: (n.position, n.key))
        block.append([EV_NOTES, b''.join(n.pack() for n in notes)])
        i = self._pattern_insert_index()
        self.events[i:i] = block
        return pid

    # ---------- playlist ----------
    def item_size(self) -> int:
        """Bytes per playlist item: measured from the file's own items when it has any,
        else taken from its version. Layouts seen in files saved by each FL release:

            FL 20.9 and older      32 bytes
            FL 20.99 (21 beta)-24.1 60 bytes
            FL 24.2 - 2025 (25.x)  80 bytes
            FL 2026 (26.x)         88 bytes
        """
        if getattr(self, '_measured_item_size', None):
            return self._measured_item_size
        d = self.events[self._playlist_index()][1]
        for size in (88, 80, 60, 32):
            if d and len(d) % size == 0 and all(
                    d[i + 4:i + 6] == struct.pack('<H', PATTERN_BASE) for i in range(0, len(d), size)):
                self._measured_item_size = size
                return size
        parts = (self.version.split('.') + ['0', '0'])[:2]
        try:
            ver = (int(parts[0]), int(parts[1]))
        except ValueError:
            ver = (0, 0)
        if ver >= (26, 0):
            return 88
        if ver >= (24, 2):
            return 80
        if ver >= (20, 99):
            return 60
        return 32

    def _playlist_indexes(self) -> list[int]:
        return [i for i, e in enumerate(self.events) if e[0] == EV_PLAYLIST]

    def _playlist_index(self) -> int:
        """Playlist of the current arrangement (FL 20+ projects can hold several)."""
        idx = self._playlist_indexes()
        if not idx:
            raise FLPError('project has no playlist; start from a template saved in FL Studio 12 or newer')
        cur = 0
        for eid, d in self.events:
            if eid == EV_ARRANGEMENT_CURRENT:
                cur = struct.unpack('<H', d)[0]
        return idx[cur] if cur < len(idx) else idx[0]

    def clear_playlist(self) -> None:
        """Empty every arrangement's playlist."""
        self.item_size()  # remember the layout before the evidence is gone
        for i in self._playlist_indexes():
            self.events[i][1] = b''

    def set_clips(self, clips: list[Clip]) -> None:
        size = self.item_size()
        items = []
        for n, c in enumerate(sorted(clips, key=lambda c: (c.position, c.track)), start=1):
            if not 1 <= c.track <= 500:
                raise FLPError('playlist tracks are 1-500')
            head = ITEM_HEAD.pack(int(c.position), PATTERN_BASE, PATTERN_BASE + c.pattern, int(c.length),
                                  500 - c.track, 0, bytes([120, 0]), 64, bytes([64, 100, 128, 128]))
            if size == 32:
                item = head + struct.pack('<ff', -1.0, -1.0)
            else:
                # FL 21+: offsets stored as -1 ("whole pattern"), a running clip number,
                # padding, then a version-specific tail (copied from files FL itself saved).
                item = head + b'\xff' * 8 + struct.pack('<I', n) + b'\x00' * 24
                if size == 80:
                    item += _FL24_TAIL
                elif size == 88:
                    item += _FL26_TAIL
            assert len(item) == size, (len(item), size)
            items.append(item)
        self.events[self._playlist_index()][1] = b''.join(items)

    def clips(self) -> list[Clip]:
        d = self.events[self._playlist_index()][1]
        size = self.item_size()
        out = []
        for i in range(0, len(d) - len(d) % size, size):
            pos, _base, idx, length, rv, *_ = ITEM_HEAD.unpack(d[i:i + 24])
            if idx > PATTERN_BASE:
                out.append(Clip(idx - PATTERN_BASE, 500 - rv, pos, length))
        return out

    def clear_markers(self) -> None:
        """Remove playlist time markers (an end or loop marker would stretch the render)."""
        out, skip = [], False
        for e in self.events:
            if e[0] == EV_MARKER_POS:
                skip = True
                continue
            if skip and e[0] in (EV_MARKER_NAME, EV_MARKER_NUM, EV_MARKER_DEN):
                continue
            skip = False
            out.append(e)
        self.events = out

    def clear_track_names(self) -> None:
        self.events = [e for e in self.events if e[0] != EV_TRACK_NAME]

    def set_track_name(self, track: int, name: str) -> None:
        """Name a playlist track (1-based)."""
        seen = 0
        for i, e in enumerate(self.events):
            if e[0] == EV_TRACK_DATA:
                seen += 1
                if seen == track:
                    nxt = self.events[i + 1] if i + 1 < len(self.events) else None
                    if nxt and nxt[0] == EV_TRACK_NAME:
                        nxt[1] = _to_utf16(name)
                    else:
                        self.events.insert(i + 1, [EV_TRACK_NAME, _to_utf16(name)])
                    return
        # older projects without per-track events: silently skip

    def summary(self) -> dict:
        return {
            'version': self.version,
            'ppq': self.ppq,
            'tempo': self.tempo,
            'channels': [{'id': c.id, 'name': c.label, 'plugin': c.plugin,
                          'sample': Path(c.sample).name if c.sample else ''} for c in self.channels()],
            'patterns': len(self.pattern_ids()),
            'clips': len(self.clips()),
        }
