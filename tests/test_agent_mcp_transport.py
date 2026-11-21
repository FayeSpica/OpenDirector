import asyncio
from contextlib import suppress
import json
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest

from opendirector.agent.gateway import ToolResult
from opendirector.agent.mcp_transport import TurnMcp


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def tool_request():
    return {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': 'read_project', 'arguments': {}}}


@pytest.mark.anyio
async def test_mcp_rejects_invalid_unicode_before_tracing_tool_arguments():
    queue = asyncio.Queue()
    mcp = TurnMcp(SimpleNamespace(), queue, 'turn-secret')
    url = await mcp.start()
    try:
        request = tool_request()
        request['params']['arguments'] = {'invalid': '\ud800'}
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.post(url, content=json.dumps(request), headers={'Authorization': 'Bearer turn-secret'})
        assert response.status_code == 400
        assert response.json()['error']['code'] == -32700
        assert queue.empty()
    finally:
        await mcp.close()


@pytest.mark.anyio
async def test_mcp_unexpected_tool_failure_has_response_event_and_log(caplog):
    async def fail(*args):
        raise RuntimeError('internal test detail')

    queue = asyncio.Queue()
    mcp = TurnMcp(SimpleNamespace(call=fail), queue, 'turn-secret')
    url = await mcp.start()
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.post(url, json=tool_request(), headers={'Authorization': 'Bearer turn-secret'})
        assert response.status_code == 200
        payload = response.json()
        assert payload['id'] == 1 and payload['result']['isError']
        detail = json.loads(payload['result']['content'][0]['text'])
        assert 'internal test detail' not in response.text
        assert 'Check project state before retrying.' in detail['error']
        started, failed = queue.get_nowait(), queue.get_nowait()
        assert (started['status'], failed['status']) == ('running', 'failed')
        assert started['id'] == failed['id'] == 'mcp:1'
        assert failed['error'] == detail['error']
        assert 'MCP tool did not return a usable result' in caplog.text
    finally:
        await mcp.close()


@pytest.mark.anyio
async def test_mcp_teardown_does_not_wait_for_peer_to_read_result():
    async def read_project(*args):
        return ToolResult(value='x' * (8 * 1024 * 1024))

    queue = asyncio.Queue()
    mcp = TurnMcp(SimpleNamespace(call=read_project), queue, 'turn-secret')
    url = urlsplit(await mcp.start())
    _, writer = await asyncio.open_connection(url.hostname, url.port, limit=1024)
    try:
        body = json.dumps(tool_request()).encode()
        headers = (f'POST /mcp HTTP/1.1\r\nHost: {url.netloc}\r\nAuthorization: Bearer turn-secret\r\n'
                   f'Content-Length: {len(body)}\r\n\r\n')
        writer.write(headers.encode() + body)
        await writer.drain()
        await asyncio.wait_for(queue.get(), timeout=5)
        assert (await asyncio.wait_for(queue.get(), timeout=5))['status'] == 'complete'
        await asyncio.wait_for(mcp.close(), timeout=1)
    finally:
        writer.transport.abort()
        with suppress(OSError):
            await writer.wait_closed()
        await mcp.close()
