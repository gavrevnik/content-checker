"""Bounded public-source reader and small HTML tree for provider adapters."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser


class SourceError(ValueError):
    pass


class Node:
    def __init__(self, tag='', attrs=(), parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children = []

    def text(self):
        return re.sub(r'\s+', ' ', ' '.join(c.text() if isinstance(c, Node) else c for c in self.children)).strip()

    def all(self, tag=None, cls=None):
        for c in self.children:
            if isinstance(c, Node):
                if (tag is None or c.tag == tag) and (cls is None or cls in c.attrs.get('class', '').split()):
                    yield c
                yield from c.all(tag, cls)


class Tree(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = self.current = Node()
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs, self.current)
        self.current.children.append(n)
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            self.current = n

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        node = self.current
        while node.parent:
            if node.tag == tag:
                self.current = node.parent
                return
            node = node.parent

    def handle_data(self, data):
        self.current.children.append(data)


def clean(html):
    return Tree(html).root.text()


class SameHostRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlparse(newurl)
        if target.scheme != 'https' or target.hostname != urllib.parse.urlparse(req.full_url).hostname:
            raise SourceError('Источник перенаправил запрос на другой хост')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, headers=None, json_response=False, payload=None):
    # Callers construct URLs from fixed provider endpoints, never user supplied hosts.
    request = urllib.request.Request(url, data=json.dumps(payload).encode() if payload is not None else None, headers={
        'User-Agent': 'ContentChecker/1.0 (personal film research)',
        'Accept': 'application/json' if json_response else 'text/html', **({'Content-Type': 'application/json'} if payload is not None else {}), **(headers or {})})
    try:
        with urllib.request.build_opener(SameHostRedirect()).open(request, timeout=15) as response:
            raw = response.read(4_000_001)
            if len(raw) > 4_000_000:
                raise SourceError('Ответ источника превышает 4 MB')
            charset = response.headers.get_content_charset() or ('windows-1251' if 'rutracker.org' in url else 'utf-8')
            body = raw.decode(charset, errors='replace')
            return json.loads(body) if json_response else body
    except urllib.error.HTTPError as error:
        raise SourceError(f'Источник вернул HTTP {error.code}; проверка недоступна') from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        # Never echo request URLs, headers or provider response bodies containing keys.
        raise SourceError(f'Не удалось прочитать источник ({type(error).__name__})') from None
