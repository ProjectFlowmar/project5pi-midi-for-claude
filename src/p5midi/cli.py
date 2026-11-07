"""p5midi command line.

  p5midi templates                      list FL Studio templates on this machine
  p5midi inspect <template|file.flp>    show channels (instruments) to use in a spec
  p5midi build song.json [-o out.flp]   write an FL Studio project from a spec
  p5midi make song.json                 build + render + master + analyze in one go
  p5midi render file.flp [--format wav] render with FL Studio (Windows)
  p5midi analyze file.wav [--reference ref.mp3]
  p5midi master in.wav out.mp3 [--lufs -10] [--ceiling -1]
  p5midi midi song.json [-o out.mid]    export the arrangement as a MIDI file (any DAW)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def _out_for(spec_path: str, out: str | None, ext: str) -> Path:
    if out:
        return Path(out)
    return Path(spec_path).with_suffix(ext) if Path(spec_path).exists() else Path('song' + ext)


def make(spec_path: str, out: str | None = None, lufs: float = -10.0, ceiling: float = -1.0,
         reference: str | None = None, skip_render: bool = False, midi: bool = True) -> dict:
    from .song import build
    from .fl import render
    from .analyze import analyze, master
    from .midi import export_midi

    res = build(spec_path)
    flp = _out_for(spec_path, out, '.flp')
    flp.parent.mkdir(parents=True, exist_ok=True)
    res.project.save(flp)
    report = {'project': str(flp.resolve()), **res.report()}
    if midi:
        report['midi'] = str(export_midi(res, flp.with_suffix('.mid')).resolve())
    if skip_render:
        return report
    wav = render(flp, 'wav')
    report['render'] = str(wav)
    mp3 = flp.with_suffix('.mp3')
    report['master'] = master(wav, mp3, lufs=lufs, ceiling_db=ceiling)
    report['analysis'] = analyze(mp3, res.sections, reference)
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog='p5midi', description='Write, render and check FL Studio songs from JSON.')
    ap.add_argument('--version', action='version', version=f'p5midi {__version__}')
    sub = ap.add_subparsers(dest='cmd', required=True)

    sub.add_parser('templates', help='list FL Studio templates')
    p = sub.add_parser('inspect', help='show channels in a template or project')
    p.add_argument('target')
    p = sub.add_parser('build', help='spec -> .flp')
    p.add_argument('spec'); p.add_argument('-o', '--out')
    p = sub.add_parser('make', help='build + render + master + analyze')
    p.add_argument('spec'); p.add_argument('-o', '--out')
    p.add_argument('--lufs', type=float, default=-10.0); p.add_argument('--ceiling', type=float, default=-1.0)
    p.add_argument('--reference'); p.add_argument('--no-render', action='store_true')
    p = sub.add_parser('render', help='render a .flp with FL Studio')
    p.add_argument('flp'); p.add_argument('--format', default='wav'); p.add_argument('--timeout', type=int, default=600)
    p = sub.add_parser('analyze', help='loudness, tonal balance, stereo, sections')
    p.add_argument('audio'); p.add_argument('--reference'); p.add_argument('--spec', help='spec, for per-section levels')
    p = sub.add_parser('master', help='loudness-normalise and limit')
    p.add_argument('src'); p.add_argument('dst')
    p.add_argument('--lufs', type=float, default=-10.0); p.add_argument('--ceiling', type=float, default=-1.0)
    p = sub.add_parser('midi', help='spec -> .mid')
    p.add_argument('spec'); p.add_argument('-o', '--out')

    a = ap.parse_args(argv)
    try:
        if a.cmd == 'templates':
            from .fl import list_templates
            _print(list_templates())
        elif a.cmd == 'inspect':
            from .fl import find_template
            from .flp import Project
            _print(Project.load(find_template(a.target)).summary())
        elif a.cmd == 'build':
            from .song import build
            res = build(a.spec)
            out = _out_for(a.spec, a.out, '.flp')
            out.parent.mkdir(parents=True, exist_ok=True)
            res.project.save(out)
            _print({'project': str(out.resolve()), **res.report()})
        elif a.cmd == 'make':
            _print(make(a.spec, a.out, a.lufs, a.ceiling, a.reference, a.no_render))
        elif a.cmd == 'render':
            from .fl import render
            print(render(a.flp, a.format, a.timeout))
        elif a.cmd == 'analyze':
            from .analyze import analyze
            sections = None
            if a.spec:
                from .song import build
                sections = build(a.spec).sections
            _print(analyze(a.audio, sections, a.reference))
        elif a.cmd == 'master':
            from .analyze import master
            _print(master(a.src, a.dst, a.lufs, a.ceiling))
        elif a.cmd == 'midi':
            from .song import build
            from .midi import export_midi
            res = build(a.spec)
            print(export_midi(res, _out_for(a.spec, a.out, '.mid')))
    except Exception as e:  # one clean line for humans and for the AI driving the tool
        print(f'error: {e}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
