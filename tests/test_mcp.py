import asyncio
import json

import pytest

pytest.importorskip('mcp')


def test_mcp_tools_and_build(tmp_path, flp_file):
    from p5midi.mcp_server import mcp
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert {'list_templates', 'inspect_template', 'spec_format', 'build_song', 'make_song',
            'render', 'analyze_audio', 'master_audio'} <= names
    spec = {'template': str(flp_file()), 'bpm': 90,
            'parts': {'k': {'type': 'drums', 'channel': 'Kick', 'lanes': {'kick': 'x...'}}}}
    res = asyncio.run(mcp.call_tool('build_song', {'spec': json.dumps(spec), 'out_path': str(tmp_path / 's.flp')}))
    assert (tmp_path / 's.flp').exists() and (tmp_path / 's.mid').exists()
