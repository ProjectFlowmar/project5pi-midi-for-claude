import shutil
import subprocess

import pytest

from p5midi.analyze import analyze, loudness, master

pytestmark = pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg not installed')


@pytest.fixture
def tone(tmp_path):
    p = tmp_path / 'tone.wav'
    # 4 s: quiet 220 Hz for 2 s, then loud 220 Hz + 5 kHz for 2 s
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi', '-i',
                    'sine=f=220:d=4,volume=0.05', '-f', 'lavfi', '-i', 'sine=f=5000:d=4,volume=0.3',
                    '-filter_complex', "[0][1]amix=inputs=2:normalize=0,volume='if(lt(t,2),0.2,1)':eval=frame,"
                    'pan=stereo|c0=c0|c1=c0', str(p)], check=True)
    return p


def test_analyze_reports_bands_and_sections(tone):
    rep = analyze(tone, [{'name': 'a', 'start_s': 0, 'end_s': 2, 'parts': ['x']},
                         {'name': 'b', 'start_s': 2, 'end_s': 4, 'parts': ['x', 'y']}])
    assert rep['duration_s'] == pytest.approx(4, abs=0.1)
    assert rep['bands_db']['presence'] > rep['bands_db']['sub']
    a, b = rep['sections']
    assert b['lufs'] > a['lufs'] + 6
    assert rep['stereo']['side_minus_mid_db'] is None or rep['stereo']['side_minus_mid_db'] < -40  # mono


def test_master_hits_target_under_ceiling(tone, tmp_path):
    out = tmp_path / 'm.wav'
    res = master(tone, out, lufs=-14, ceiling_db=-1)
    assert res['lufs'] == pytest.approx(-14, abs=0.6)
    assert res['true_peak_db'] <= -1
    assert loudness(out)['lufs'] == res['lufs']
