"""A tiny synthetic FL project, so tests run anywhere without FL Studio or its files."""
import struct

import pytest

from p5midi.flp import serialize_events


def u16(s):
    return (s + '\0').encode('utf-16-le')


def make_flp(version='20.7.0.1702', ppq=96, channels=('Kick', 'Bass', 'Pad'), arrangements=1,
             current=None, tracks=4, items=b'', markers=False):
    ev = [[199, (version + '\0').encode()], [156, struct.pack('<I', 120000)], [9, b'\x00'],
          [194, u16('Template title')], [195, u16('Template comment')]]
    plugins = {'Kick': 'Fruit Kick', 'Bass': '3x Osc', 'Pad': 'Sytrus'}
    for i, name in enumerate(channels):
        ev += [[64, struct.pack('<H', i)], [21, b'\x02'], [201, u16(plugins.get(name, 'Sampler'))],
               [203, u16(name)]]
    ev += [[236, b'\x00' * 4], [203, u16('Mixer slot plugin')]]
    for a in range(arrangements):
        ev += [[99, struct.pack('<H', a)], [241, u16('Arrangement')], [233, items if a == 0 else b'']]
        if markers:
            ev += [[148, struct.pack('<I', 0x04000000 | 3840)], [33, b'\x04'], [34, b'\x04']]
        for t in range(tracks):
            ev += [[238, struct.pack('<I', t) + b'\x00' * 62], [239, u16(f'Old track {t + 1}')]]
    if current is not None:
        ev.append([100, struct.pack('<H', current)])
    header = struct.pack('<HHH', 0, len(channels), ppq)
    return serialize_events(header, ev)


@pytest.fixture
def flp_file(tmp_path):
    def _make(**kw):
        p = tmp_path / f"t{len(list(tmp_path.iterdir()))}.flp"
        p.write_bytes(make_flp(**kw))
        return p
    return _make
