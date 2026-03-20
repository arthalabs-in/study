from __future__ import annotations

import urllib.error
from types import SimpleNamespace

import src.web_search as web_search


class _Response:
    def __init__(self, url: str, body: str, content_type: str = 'text/html') -> None:
        self._url = url
        self.headers = {'Content-Type': content_type}
        self._body = body.encode('utf-8')

    def geturl(self) -> str:
        return self._url

    def read(self, _: int) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _Opener:
    def __init__(self, response=None, exc=None) -> None:
        self._response = response
        self._exc = exc

    def open(self, req, timeout=5):
        if self._exc:
            raise self._exc
        return self._response


def test_is_safe_url_rejects_local_and_invalid_hosts(monkeypatch) -> None:
    monkeypatch.setattr(web_search.socket, 'getaddrinfo', lambda host, port: [(None, None, None, None, ('127.0.0.1', 0))])
    assert web_search._is_safe_url('http://localhost/test') is False
    assert web_search._is_safe_url('file:///tmp/test.txt') is False
    assert web_search._is_safe_url('http://example.com') is False


def test_is_safe_url_accepts_public_host(monkeypatch) -> None:
    monkeypatch.setattr(web_search.socket, 'getaddrinfo', lambda host, port: [(None, None, None, None, ('93.184.216.34', 0))])
    assert web_search._is_safe_url('https://example.com/page') is True


def test_fetch_page_text_refuses_redirects(monkeypatch) -> None:
    monkeypatch.setattr(web_search, '_is_safe_url', lambda url: True)
    redirect_error = urllib.error.HTTPError('https://example.com', 302, 'Found', {}, None)
    opener = _Opener(exc=redirect_error)
    monkeypatch.setattr(web_search.urllib.request, 'build_opener', lambda *args: opener)
    assert web_search._fetch_page_text('https://example.com') == ''


def test_fetch_page_text_strips_html(monkeypatch) -> None:
    monkeypatch.setattr(web_search, '_is_safe_url', lambda url: True)
    opener = _Opener(response=_Response('https://example.com', '<html><body><h1>Title</h1><p>' + ('A' * 120) + '</p></body></html>'))
    monkeypatch.setattr(web_search.urllib.request, 'build_opener', lambda *args: opener)
    text = web_search._fetch_page_text('https://example.com')
    assert 'Title' in text
    assert '<html>' not in text
