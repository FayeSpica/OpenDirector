"""Turn-scoped, stateless MCP endpoint for the bundled OpenCode runtime."""
import asyncio
from contextlib import suppress
import json
import logging
import secrets

from opendirector.agent.gateway import ToolGateway, ToolResult


logger = logging.getLogger(__name__)
PROTOCOL_VERSIONS = ('2025-11-25', '2025-06-18', '2025-03-26')
REQUEST_LIMIT = 4 * 1024 * 1024


class TurnMcp:
    def __init__(self, gateway: ToolGateway, queue: asyncio.Queue, token: str):
        self.gateway = gateway
        self.queue = queue
        self.authorization = f'Bearer {token}'
        self.clients: dict[asyncio.Task, asyncio.StreamWriter] = {}
        self.closed = False

    async def start(self) -> str:
        self.server = await asyncio.start_server(self.connect, '127.0.0.1', 0, limit=16384)
        port = self.server.sockets[0].getsockname()[1]
        self.host = f'127.0.0.1:{port}'
        return f'http://{self.host}/mcp'

    def connect(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self.closed:
            writer.close()
            return
        task = asyncio.create_task(self.request(reader, writer))
        self.clients[task] = writer
        task.add_done_callback(self.clients.pop)

    async def close(self) -> None:
        self.closed = True
        self.server.close()
        clients = tuple(self.clients.items())
        for client, writer in clients:
            # Closing normally can wait for a stopped client to read buffered results.
            writer.transport.abort()
            client.cancel()
        await asyncio.gather(*(client for client, _ in clients), return_exceptions=True)
        await self.server.wait_closed()

    async def response(self, reader: asyncio.StreamReader) -> tuple[str, dict | None]:
        try:
            async with asyncio.timeout(15):
                block = await reader.readuntil(b'\r\n\r\n')
                request_line, *lines = block.decode('ascii').strip().split('\r\n')
                headers = {}
                for line in lines:
                    key, value = line.split(':', 1)
                    key = key.lower()
                    if key in headers:
                        return '400 Bad Request', None
                    headers[key] = value.strip()
                # Only the native subprocess uses this endpoint.
                if ('origin' in headers or headers.get('host') != self.host or
                        not secrets.compare_digest(headers.get('authorization', ''), self.authorization)):
                    return '403 Forbidden', None
                method, path, version = request_line.split()
                if path != '/mcp' or version != 'HTTP/1.1':
                    return '404 Not Found', None
                if method != 'POST':
                    return '405 Method Not Allowed', None
                length = int(headers.get('content-length', '0'))
                if 'transfer-encoding' in headers or not 0 < length <= REQUEST_LIMIT:
                    return '400 Bad Request', None
                data = await reader.readexactly(length)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, UnicodeError, ValueError, TimeoutError):
            return '400 Bad Request', None
        try:
            message = json.loads(data)
            # Model input must also be safe to serialize in tool events and receipts.
            json.dumps(message, ensure_ascii=False, allow_nan=False).encode('utf-8')
        except (ValueError, RecursionError):
            return '400 Bad Request', {'jsonrpc': '2.0', 'id': None,
                                       'error': {'code': -32700, 'message': 'Parse error'}}
        return await self.dispatch(message)

    async def request(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            status, message = await self.response(reader)
            body = json.dumps(message, ensure_ascii=False).encode() if message is not None else b''
            writer.write((f'HTTP/1.1 {status}\r\nContent-Type: application/json\r\n'
                          f'Content-Length: {len(body)}\r\nCache-Control: no-store\r\n'
                          'Connection: close\r\n\r\n').encode() + body)
            await writer.drain()
        except OSError:
            # The caller can disconnect after a write has been accepted.
            pass
        finally:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()

    async def dispatch(self, message) -> tuple[str, dict | None]:
        if not isinstance(message, dict) or message.get('jsonrpc') != '2.0' or not isinstance(message.get('method'), str):
            return '400 Bad Request', {'jsonrpc': '2.0', 'id': None,
                                       'error': {'code': -32600, 'message': 'Invalid Request'}}
        if 'id' not in message:
            return '202 Accepted', None
        request_id = message['id']
        if type(request_id) not in (str, int) or len(str(request_id)) > 500:
            return '400 Bad Request', {'jsonrpc': '2.0', 'id': None,
                                       'error': {'code': -32600, 'message': 'Invalid Request'}}
        envelope = {'jsonrpc': '2.0', 'id': request_id}
        params = message.get('params', {})
        if not isinstance(params, dict):
            return '200 OK', {**envelope, 'error': {'code': -32602, 'message': 'Invalid params'}}
        method = message['method']
        if method == 'initialize':
            version = params.get('protocolVersion')
            result = {'protocolVersion': version if version in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                      'capabilities': {'tools': {}}, 'serverInfo': {'name': 'OpenDirector', 'version': '1'}}
        elif method == 'ping':
            result = {}
        elif method == 'tools/list':
            functions = (tool['function'] for tool in self.gateway.catalog)
            result = {'tools': [
                {'name': function['name'], 'description': function['description'],
                 'inputSchema': function['parameters']}
                for function in functions if function['name'] != 'command_schema'
            ]}
        elif method == 'tools/call':
            name = params.get('name')
            arguments = params.get('arguments', {})
            if not isinstance(name, str) or not isinstance(arguments, dict):
                return '200 OK', {**envelope, 'error': {'code': -32602, 'message': 'Invalid params'}}
            result = await self.call(name, arguments, request_id)
        else:
            return '200 OK', {**envelope, 'error': {'code': -32601, 'message': 'Method not found'}}
        return '200 OK', {**envelope, 'result': result}

    async def call(self, name: str, arguments: dict, request_id: int | str) -> dict:
        call_id = 'mcp:' + json.dumps(request_id, ensure_ascii=False)
        await self.queue.put({'type': 'tool', 'id': call_id, 'name': name,
                              'status': 'running', 'arguments': arguments})
        try:
            outcome = await self.gateway.call(call_id, name, arguments)
            detail = json.dumps(outcome.value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
            detail.encode('utf-8')
        except Exception:
            logger.exception('MCP tool did not return a usable result')
            message = 'Tool result unavailable. Check project state before retrying.'
            outcome = ToolResult(value={'error': message}, error=message)
            detail = json.dumps(outcome.value, separators=(',', ':'))
        await self.queue.put({**outcome.event(name), 'id': call_id, 'arguments': arguments,
                              'result': detail[:65536], 'resultTruncated': len(detail) > 65536})
        return {'content': [{'type': 'text', 'text': detail}], 'isError': bool(outcome.error)}
