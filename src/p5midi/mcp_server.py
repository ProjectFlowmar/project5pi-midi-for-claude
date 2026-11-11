"""MCP server: gives Claude (Desktop or Code) tools to compose with FL Studio.

Run:  p5midi-mcp          (stdio transport)
Register in Claude Code:  claude mcp add p5midi -- p5midi-mcp
"""
from __future__ import annotations

import json
from pathlib import Path

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:
    try:  # MCP Python SDK 1.x
        from mcp.server.fastmcp import FastMCP
    except ImportError as e:  # pragma: no cover
        raise SystemExit('The MCP server needs the "mcp" extra: pip install "project5pi-midi-for-claude[mcp]"') from e

from . import fl as _fl
from .flp import Project

SPEC_HELP = (Path(__file__).with_name('SPEC.md')).read_text(encoding='utf-8') \
    if Path(__file__).with_name('SPEC.md').exists() else 'See docs/SPEC.md in the repository.'

mcp = FastMCP('p5midi', instructions=(
    'Compose music in FL Studio. Workflow: list_templates -> inspect_template (learn channel names) -> '
    'write a song spec (call spec_format for the format) -> make_song (builds, renders, masters, analyzes) -> '
    'read the analysis notes, adjust the spec, repeat. You cannot hear the audio: rely on the analysis numbers '
    'and ask the user to listen before calling a song finished.'))

# Tool hints for clients (MCP ToolAnnotations). Every tool works on local files only (openWorld False);
# the writers overwrite their output path (destructive) but give the same result when re-run (idempotent).
READ = {'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False}
WRITE = {'readOnlyHint': False, 'destructiveHint': True, 'idempotentHint': True, 'openWorldHint': False}


def _tool(hints: dict):
    try:
        from mcp.types import ToolAnnotations
        return mcp.tool(annotations=ToolAnnotations.model_validate(hints))
    except (ImportError, TypeError):  # older SDKs without annotations
        return mcp.tool()


@_tool(READ)
def list_templates() -> list[dict]:
    """FL Studio templates on this machine. Each keeps its instruments, mixer and effects."""
    return _fl.list_templates()


@_tool(READ)
def inspect_template(template: str) -> dict:
    """Channels (instrument names and plugins) in a template name or .flp path."""
    return Project.load(_fl.find_template(template)).summary()


@_tool(READ)
def spec_format() -> str:
    """The song spec JSON format, with examples."""
    return SPEC_HELP


@_tool(WRITE)
def build_song(spec: dict | str, out_path: str) -> dict:
    """Write an .flp (and .mid) from a spec, without rendering. out_path ends in .flp."""
    from .song import build
    from .midi import export_midi
    res = build(spec if isinstance(spec, dict) else json.loads(spec))
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    res.project.save(out)
    return {'project': str(out.resolve()), 'midi': str(export_midi(res, out.with_suffix('.mid'))), **res.report()}


@_tool(WRITE)
def make_song(spec: dict | str, out_path: str, lufs: float = -10.0, reference: str | None = None) -> dict:
    """Build + render with FL Studio + master to MP3 + analyze. Needs Windows, FL Studio closed, ffmpeg."""
    from .cli import make
    tmp = Path(out_path).with_suffix('.json')
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(spec if isinstance(spec, str) else json.dumps(spec, indent=2), encoding='utf-8')
    return make(str(tmp), out_path, lufs=lufs, reference=reference)


@_tool(WRITE)
def render(flp_path: str, fmt: str = 'wav') -> str:
    """Render an existing .flp with FL Studio's command line. Returns the audio path."""
    return str(_fl.render(flp_path, fmt))


@_tool(READ)
def analyze_audio(path: str, reference: str | None = None) -> dict:
    """Loudness, true peak, six-band balance, stereo width; optional comparison to a reference track."""
    from .analyze import analyze
    return analyze(path, None, reference)


@_tool(WRITE)
def master_audio(src: str, dst: str, lufs: float = -10.0, ceiling_db: float = -1.0) -> dict:
    """Loudness-normalise and limit a render (mp3/wav/flac by extension)."""
    from .analyze import master
    return master(src, dst, lufs, ceiling_db)


def main() -> None:
    mcp.run()


if __name__ == '__main__':
    main()
