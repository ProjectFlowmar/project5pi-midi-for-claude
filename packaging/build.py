"""Build dist/P5-MIDI-Studio-Setup-<version>.exe (Windows).

    py -3.12 -m venv build/venv && build\venv\Scripts\pip install pyinstaller pillow -e .
    build\venv\Scripts\python packaging/build.py
Needs Inno Setup 6 (winget install JRSoftware.InnoSetup).
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from p5midi import __version__  # noqa: E402

PKG = ROOT / 'packaging'
subprocess.run([sys.executable, str(PKG / 'make_icon.py')], check=True)
work = ROOT / 'build' / 'pyi'
data = [(ROOT / 'src' / 'p5midi' / 'studio.html', 'p5midi'), (ROOT / 'src' / 'p5midi' / 'SPEC.md', 'p5midi')]
subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed', '--onedir',
                '--name', 'P5 MIDI Studio', '--icon', str(PKG / 'p5midi.ico'),
                '--paths', str(ROOT / 'src'), '--collect-submodules', 'p5midi',
                *[a for f, dest in data for a in ('--add-data', f'{f}{os.pathsep}{dest}')],
                '--distpath', str(work / 'dist'), '--workpath', str(work / 'work'), '--specpath', str(work),
                str(PKG / 'launcher.py')], check=True)
shutil.copy(ROOT / 'LICENSE', work / 'dist' / 'P5 MIDI Studio' / 'LICENSE.txt')
iscc = next((p for p in [Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'Inno Setup 6' / 'ISCC.exe',
                          Path(r'C:\Program Files (x86)\Inno Setup 6\ISCC.exe')] if p.exists()), None)
if not iscc:
    sys.exit('Inno Setup 6 not found (winget install JRSoftware.InnoSetup)')
subprocess.run([str(iscc), '/Q', str(PKG / 'installer.iss')], check=True, env={**os.environ, 'P5MIDI_VERSION': __version__})
out = ROOT / 'dist' / f'P5-MIDI-Studio-Setup-{__version__}.exe'
print(out, f'{out.stat().st_size / 1e6:.1f} MB')
