"""Find FL Studio and its templates, and render projects from the command line."""
from __future__ import annotations

import os
import shutil
import string
import subprocess
import sys
import time
from pathlib import Path


class FLNotFound(RuntimeError):
    pass


def _candidate_roots() -> list[Path]:
    roots = []
    if sys.platform == 'win32':
        for drive in string.ascii_uppercase:
            for pf in ('Program Files', 'Program Files (x86)'):
                p = Path(f'{drive}:/{pf}/Image-Line')
                if p.is_dir():
                    roots.append(p)
    else:
        roots.append(Path('/Applications'))
    return roots


def find_fl_exe() -> Path:
    """FL64.exe path. Set FL_STUDIO_EXE to override."""
    env = os.environ.get('FL_STUDIO_EXE')
    if env:
        p = Path(env)
        if p.is_file():
            return p
        raise FLNotFound(f'FL_STUDIO_EXE points to a missing file: {env}')
    found = []
    for root in _candidate_roots():
        found += list(root.glob('FL Studio*/FL64.exe'))
    if not found:
        raise FLNotFound('FL Studio not found. Install it, or set FL_STUDIO_EXE to the full path of FL64.exe')

    def ver(p: Path):  # prefer the newest: "FL Studio 2026" > "FL Studio 21" > "FL Studio 20"
        digits = ''.join(ch for ch in p.parent.name if ch.isdigit())
        n = int(digits) if digits else 0
        return n if n > 1000 else n + 1990  # 21 -> 2011-ish, below year-named releases
    return max(found, key=ver)


def template_dirs() -> list[Path]:
    dirs = []
    try:
        dirs.append(find_fl_exe().parent / 'Data' / 'Templates')
    except FLNotFound:
        pass
    for app in Path('/Applications').glob('FL Studio*.app') if sys.platform == 'darwin' else []:
        dirs.append(app / 'Contents' / 'Resources' / 'FL' / 'Data' / 'Templates')
    home = Path.home()
    for docs in (home / 'Documents', home / 'OneDrive' / 'Documents'):
        p = docs / 'Image-Line' / 'FL Studio' / 'Projects' / 'Templates'
        if p.is_dir():
            dirs.append(p)
    extra = os.environ.get('P5MIDI_TEMPLATES')
    if extra:
        dirs += [Path(x) for x in extra.split(os.pathsep)]
    return [d for d in dirs if d.is_dir()]


def list_templates() -> list[dict]:
    out = []
    for d in template_dirs():
        for f in sorted(d.rglob('*.flp')):
            out.append({'name': f.stem, 'group': str(f.parent.relative_to(d).parent).replace('\\', '/'),
                        'path': str(f)})
    return out


def find_template(name: str) -> Path:
    p = Path(name)
    if p.suffix.lower() == '.flp' and p.is_file():
        return p
    temps = list_templates()
    low = name.lower().removesuffix('.flp')
    for t in temps:
        if t['name'].lower() == low:
            return Path(t['path'])
    for t in temps:
        if low in t['name'].lower():
            return Path(t['path'])
    names = ', '.join(sorted({t['name'] for t in temps})) or '(none found)'
    raise FileNotFoundError(f'template {name!r} not found. Available: {names}')


def fl_is_running() -> bool:
    if sys.platform != 'win32':
        return False
    try:
        out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq FL64.exe', '/NH'], capture_output=True,
                             text=True, timeout=15, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
        return 'FL64.exe' in out
    except (OSError, subprocess.SubprocessError):
        return False


def render(flp: str | Path, fmt: str = 'wav', timeout: int = 600) -> Path:
    """Render a project with FL Studio's command line (FL64.exe /R /E<fmt> "<file>").

    FL writes the audio next to the project, with the same name. FL must be closed first:
    a running copy of FL Studio grabs the command instead of rendering.
    Formats FL accepts: wav, mp3, ogg, flac (comma separated for several).
    """
    flp = Path(flp).resolve()
    if not flp.is_file():
        raise FileNotFoundError(flp)
    if sys.platform != 'win32':
        raise FLNotFound('command-line rendering is only supported on Windows; open the .flp in FL Studio '
                         'and export it there')
    if fl_is_running():
        raise RuntimeError('FL Studio is open. Close it first (it intercepts the render command), '
                           'or open the .flp yourself and export.')
    exe = find_fl_exe()
    first = fmt.split(',')[0].strip().lower()
    out = flp.with_suffix('.' + first)
    before = out.stat().st_mtime if out.exists() else 0
    t0 = time.time()
    proc = subprocess.Popen([str(exe), '/R', f'/E{fmt}', str(flp)])
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise TimeoutError(f'FL Studio did not finish rendering within {timeout}s '
                           '(a dialog may be open; check the FL window)') from None
    if not out.exists() or out.stat().st_mtime <= before:
        raise RuntimeError(f'FL Studio exited after {time.time() - t0:.0f}s without writing {out.name}. '
                           'Check the latest log in Documents/Image-Line/FL Studio/Support/Logs/Startup')
    return out


def ffmpeg() -> str | None:
    exe = os.environ.get('FFMPEG') or shutil.which('ffmpeg')
    if not exe and sys.platform == 'win32':  # installed by winget after this process started (PATH is stale)
        local = Path(os.environ.get('LOCALAPPDATA', '')) / 'Microsoft' / 'WinGet'
        cands = [local / 'Links' / 'ffmpeg.exe'] + sorted((local / 'Packages').glob('Gyan.FFmpeg*/*/bin/ffmpeg.exe'))
        exe = next((str(c) for c in cands if c.is_file()), None)
    if not exe:
        raise FileNotFoundError('ffmpeg not found on PATH (needed for analyze/master). '
                                'Install it from https://ffmpeg.org or `winget install ffmpeg`')
    return exe


def install_ffmpeg(timeout: int = 900) -> str:
    """Install a small ffmpeg build with winget (Windows 10/11), no admin rights needed."""
    if sys.platform != 'win32':
        raise RuntimeError('Install ffmpeg with your package manager (brew install ffmpeg / apt install ffmpeg).')
    winget = shutil.which('winget') or str(Path(os.environ.get('LOCALAPPDATA', '')) / 'Microsoft' / 'WindowsApps' / 'winget.exe')
    p = subprocess.run([winget, 'install', '--id', 'Gyan.FFmpeg.Essentials', '-e', '--scope', 'user', '--silent',
                        '--accept-package-agreements', '--accept-source-agreements'], capture_output=True, text=True,
                       timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        return ffmpeg()
    except FileNotFoundError:
        raise RuntimeError('Could not install ffmpeg automatically: ' + (p.stdout + p.stderr)[-300:].strip()) from None
