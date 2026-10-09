#!/usr/bin/env python3
"""Dependency-free MCP stdio bridge to the running local Content Checker API.

Implements the 2025-03-26 lifecycle and tools capability only. Stdout is JSON-RPC.
The bridge never opens/migrates SQLite or starts/stops the web service.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.runtime import ensure_connectors
if __name__ == "__main__":
    ensure_connectors()

from app.research import INSTRUCTIONS, TOOLS, validate


class Bridge:
    def __init__(self, base_url):
        p = urllib.parse.urlparse(base_url)
        if p.scheme != 'http' or p.hostname not in {'127.0.0.1', 'localhost', '::1'} or p.username or p.password or p.path not in {'', '/'} or p.query or p.fragment:
            raise ValueError('MCP API URL must be loopback HTTP with no path or credentials')
        self.base_url = base_url.rstrip('/')
        self.initialized = False
        self.ready = False

    def api(self, path, payload=None):
        request = urllib.request.Request(self.base_url + path, data=json.dumps(payload).encode() if payload is not None else None,
                                         headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                return json.loads(response.read(5_000_000))
        except urllib.error.HTTPError as error:
            try:
                message = json.loads(error.read(4096)).get('error', f'HTTP {error.code}')
            except ValueError:
                message = f'HTTP {error.code}'
            raise ValueError(message) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise ValueError('Content Checker недоступен. Запустите актуальный сервис через Life Hub / Whats New Checker.app.') from None

    def handle(self, request):
        if not isinstance(request, dict) or request.get('jsonrpc') != '2.0' or not isinstance(request.get('method'), str):
            return {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'Invalid request'}}
        method, rid = request['method'], request.get('id')
        if 'id' not in request:
            if method == 'notifications/initialized' and self.initialized:
                self.ready = True
            return None
        base = {'jsonrpc': '2.0', 'id': rid}
        params = request.get('params', {})
        if not isinstance(params, dict):
            return {**base, 'error': {'code': -32602, 'message': 'params must be an object'}}
        if method == 'initialize':
            self.initialized = True
            return {**base, 'result': {'protocolVersion': '2025-03-26', 'capabilities': {'tools': {'listChanged': False}},
                                     'serverInfo': {'name': 'content-checker', 'version': '1.1.0'}, 'instructions': INSTRUCTIONS}}
        if method == 'ping':
            return {**base, 'result': {}}
        if not self.ready:
            return {**base, 'error': {'code': -32002, 'message': 'Complete initialize first'}}
        if method == 'tools/list':
            return {**base, 'result': {'tools': TOOLS}}
        if method == 'tools/call':
            try:
                name = params.get('name')
                spec = next((t for t in TOOLS if t['name'] == name), None)
                if not spec:
                    return {**base, 'error': {'code': -32602, 'message': 'Unknown tool'}}
                args = params.get('arguments', {})
                validate(args, spec['inputSchema'])
                health = self.api('/api/health')
                if health.get('application') != 'whats-new-checker' or health.get('version', 0) < 59:
                    raise ValueError('На порту другой сервис или устаревшая версия Content Checker; нужен перезапуск')
                data = self.api('/api/research/call', {'name': name, 'arguments': args})
                result = {'content': [{'type': 'text', 'text': json.dumps(data, ensure_ascii=False)}], 'isError': False}
            except ValueError as error:
                result = {'content': [{'type': 'text', 'text': str(error)}], 'isError': True}
            return {**base, 'result': result}
        return {**base, 'error': {'code': -32601, 'message': 'Method not found'}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8765')
    args = parser.parse_args()
    bridge = Bridge(args.base_url)
    while True:
        line = sys.stdin.buffer.readline(1_000_002)
        if not line:
            break
        if len(line) > 1_000_000:
            # Discard the remainder; never interpret a suffix as another request.
            while line and not line.endswith(b'\n'):
                line = sys.stdin.buffer.readline(1_000_002)
            response = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'Request too large'}}
        else:
            try:
                response = bridge.handle(json.loads(line))
            except (ValueError, UnicodeDecodeError):
                response = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Parse error'}}
            except Exception:
                response = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32603, 'message': 'Internal error'}}
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
