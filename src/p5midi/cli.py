"""p5midi command line.

  p5midi templates                      list FL Studio templates on this machine
  p5midi inspect <template|file.flp>    show channels (instruments) to use in a spec
  p5midi build song.json [-o out.flp]   write an FL Studio project from a spec
  p5midi make song.json                 build + render + master + analyze in one go
  p5midi render file.flp [--format wav] render with FL Studio (Windows)
  p5midi analyze file.wav [--reference ref.mp3]
  p5midi master in.wav out.mp3 [--lufs -10] [--ceiling -1]
  p5midi midi song.json [-o out.mid]    export the arrangement as a MIDI file (any DAW)
  p5midi create "idea" [--seconds 120]  AI writes the song, then make + automatic mix fix
  p5midi change <song folder> "darker"  AI revises the latest version
  p5midi studio                         local app: idea box, player, Open in FL Studio
  p5midi stems song.flp                 one WAV per part, same length, for any DAW
  p5midi film --srt c.srt --video v.mp4 original underscore timed to the narration (+ preview mix)
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
    p = sub.add_parser('create', help='idea -> song (needs an AI key, e.g. DEEPSEEK_API_KEY)')
    p.add_argument('idea'); p.add_argument('-o', '--out', default='songs'); p.add_argument('--seconds', type=float)
    p.add_argument('--no-fix', action='store_true'); p.add_argument('--no-render', action='store_true')
    p.add_argument('--pack', help='genre pack id (see: p5midi packs)')
    p.add_argument('--versions', type=int, default=1, help='make 1 to 3 different takes on the idea')
    p = sub.add_parser('change', help='AI revises the latest version of a song')
    p.add_argument('folder'); p.add_argument('instruction'); p.add_argument('--no-render', action='store_true')
    p = sub.add_parser('stems', help='render one WAV per part (same length, aligned)')
    p.add_argument('flp'); p.add_argument('-o', '--out')
    p = sub.add_parser('film', help='score a video: timed underscore from captions (+ optional preview mix)')
    p.add_argument('--srt', help='narration captions with times (without it, the scene cuts of the video are used)')
    p.add_argument('--brief', default='', help='one line on the feel, e.g. "uplifting product promo"')
    p.add_argument('--score', action='store_true', help='always compose an FL Studio score (default: only for 3 minutes and longer)')
    p.add_argument('--style', help='composer influence id (p5midi styles), or auto to let the AI pick')
    p.add_argument('--threshold', type=float, default=180.0, help='seconds; shorter videos get free-license music')
    p.add_argument('--video', help='the video (for its length and an optional preview mix)')
    p.add_argument('--duration', type=float, help='length in seconds if no --video')
    p.add_argument('--scenes', help='JSON with "scenes" [{start,end,type}] (docv2 props) for intro/title/act/sponsor/end')
    p.add_argument('--regions', help='JSON list [{start,end,kind}] with kind intro|silence|title|act|sponsor|end_card')
    p.add_argument('--title', default='Film score'); p.add_argument('-o', '--out', default='score')
    p.add_argument('--no-render', action='store_true'); p.add_argument('--preview', action='store_true',
                   help='also write <out>/preview.mp4: the video with the score ducked under its audio')
    p = sub.add_parser('packs', help='list the genre packs')
    p = sub.add_parser('styles', help='list the composer/producer influence database')
    p.add_argument('id', nargs='?', help='show one style in full')
    p = sub.add_parser('studio', help='open the local Studio app in your browser')
    p.add_argument('--port', type=int, default=47390); p.add_argument('--no-browser', action='store_true')

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
        elif a.cmd in ('create', 'change'):
            from . import app
            say = lambda m: print(f'... {m}', file=sys.stderr)
            if a.cmd == 'create':
                r = app.create(a.idea, a.out, max_seconds=a.seconds, fix=not a.no_fix, render=not a.no_render, step=say,
                               pack=a.pack, versions=a.versions)
            else:
                r = app.change(a.folder, a.instruction, render=not a.no_render, step=say)
            _print({k: r[k] for k in r if k != 'report'} | {'notes': (r['report'].get('analysis') or {}).get('notes')})
        elif a.cmd == 'stems':
            from .stems import export_stems
            _print(export_stems(a.flp, a.out, step=lambda m: print(f'... {m}', file=sys.stderr)))
        elif a.cmd == 'film':
            from . import film
            from .analyze import duration as media_duration
            dur = a.duration or (media_duration(a.video) if a.video else None)
            if not dur:
                raise ValueError('give --video or --duration')
            regions = []
            if a.scenes:
                regions = film.regions_from_scenes(json.loads(Path(a.scenes).read_text(encoding='utf-8'))['scenes'])
            if a.regions:
                regions += json.loads(Path(a.regions).read_text(encoding='utf-8'))
            if a.srt:
                lines = film.read_srt(a.srt)
            elif a.video and (a.score or dur >= a.threshold):
                lines = film.cut_lines(film.scene_cuts(a.video), dur)
            else:
                lines = []
            r = film.soundtrack(dur, a.out, lines, regions, a.title, a.brief, force_score=a.score, threshold=a.threshold,
                                render=not a.no_render, step=lambda m: print(f'... {m}', file=sys.stderr), style=a.style)
            if a.preview and a.video and r.get('wav'):
                r['preview'] = str(film.mix_preview(a.video, r['wav'], Path(a.out) / 'preview.mp4'))
            if 'cue_sheet' in r:
                r['cue_sheet'] = {'key': r['cue_sheet']['key'], 'source': r['cue_sheet']['source'],
                                  'cues': len(r['cue_sheet']['cues']), 'hits': r['cue_sheet']['hits']}
            _print(r)
        elif a.cmd == 'packs':
            from . import packs as _packs
            _print(_packs.menu())
        elif a.cmd == 'styles':
            from . import styles as _styles
            _print(_styles.get(a.id) if a.id else _styles.menu())
        elif a.cmd == 'studio':
            from .studio import main as studio_main
            studio_main(a.port, not a.no_browser)
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
