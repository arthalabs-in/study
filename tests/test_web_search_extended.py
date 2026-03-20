from __future__ import annotations

import socket
from types import SimpleNamespace

import src.web_search as web_search


def test_hostname_and_url_guards(monkeypatch) -> None:
    monkeypatch.setattr(web_search.socket, "getaddrinfo", lambda host, port: (_ for _ in ()).throw(socket.gaierror()))
    assert web_search._is_public_hostname("example.com") is False

    monkeypatch.setattr(web_search.socket, "getaddrinfo", lambda host, port: [(None, None, None, None, ("10.0.0.5", 0))])
    assert web_search._is_public_hostname("example.com") is False

    monkeypatch.setattr(web_search.socket, "getaddrinfo", lambda host, port: [(None, None, None, None, ("93.184.216.34", 0))])
    assert web_search._is_public_hostname("Example.com.") is True
    assert web_search._is_safe_url("https://example.com/path") is True
    assert web_search._is_safe_url("http://example.com/path") is False
    assert web_search._is_safe_url("https://user:pass@example.com/path") is False
    assert web_search._is_safe_url("https:///missing-host") is False


def test_strip_html_and_fetch_page_edge_cases(monkeypatch) -> None:
    assert web_search._strip_html("<style>bad</style><p>Hello &amp; goodbye</p>") == "Hello & goodbye"

    class Response:
        def __init__(self, url: str, body: str, content_type: str = "text/html") -> None:
            self._url = url
            self.headers = {"Content-Type": content_type}
            self._body = body.encode("utf-8")

        def geturl(self) -> str:
            return self._url

        def read(self, _: int) -> bytes:
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class Opener:
        def __init__(self, response=None, exc=None):
            self.response = response
            self.exc = exc

        def open(self, req, timeout=5):
            if self.exc:
                raise self.exc
            return self.response

    monkeypatch.setattr(web_search, "_is_safe_url", lambda url: url != "https://unsafe.example")
    monkeypatch.setattr(web_search.urllib.request, "build_opener", lambda *args: Opener(Response("https://unsafe.example", "x" * 300)))
    assert web_search._fetch_page_text("https://example.com") == ""

    monkeypatch.setattr(web_search.urllib.request, "build_opener", lambda *args: Opener(Response("https://example.com", "x" * 300, content_type="image/png")))
    assert web_search._fetch_page_text("https://example.com") == ""

    monkeypatch.setattr(web_search.urllib.request, "build_opener", lambda *args: Opener(Response("https://example.com", "too short")))
    assert web_search._fetch_page_text("https://example.com") == ""

    monkeypatch.setattr(web_search.urllib.request, "build_opener", lambda *args: Opener(exc=RuntimeError("boom")))
    assert web_search._fetch_page_text("https://example.com") == ""


def test_web_search_result_flow(monkeypatch) -> None:
    class FakeDDGS:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def text(self, query, max_results=5):
            assert max_results == 10
            return [
                {"title": "Good", "href": "https://good.example", "body": "snippet"},
                {"title": "Blocked", "href": "https://blocked.example", "body": "skip"},
            ]

    ddgs_module = type(web_search)("ddgs")
    ddgs_module.DDGS = FakeDDGS
    monkeypatch.setitem(__import__("sys").modules, "ddgs", ddgs_module)
    monkeypatch.setattr(web_search, "_is_safe_url", lambda url: "blocked" not in url)
    monkeypatch.setattr(web_search, "_fetch_page_text", lambda url: "page content" if "good" in url else "")

    results = web_search.web_search("entropy", max_results=99, fetch_pages=True)
    assert results == [{"title": "Good", "url": "https://good.example", "snippet": "snippet", "content": "page content"}]


def test_web_search_importerror_no_results_and_failure(monkeypatch) -> None:
    modules = __import__("sys").modules
    modules.pop("ddgs", None)
    modules.pop("duckduckgo_search", None)

    real_import = __import__("builtins").__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in {"ddgs", "duckduckgo_search"}:
            raise ImportError(name)
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(__import__("builtins"), "__import__", fake_import)
    assert "No search package found" in web_search.web_search("entropy")[0]["error"]

    class EmptyDDGS:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def text(self, query, max_results=5):
            return []

    ddgs_module = type(web_search)("ddgs")
    ddgs_module.DDGS = EmptyDDGS
    monkeypatch.setitem(modules, "ddgs", ddgs_module)
    monkeypatch.setattr(__import__("builtins"), "__import__", real_import)
    assert "No results found" in web_search.web_search("entropy")[0]["info"]

    class FailingDDGS(EmptyDDGS):
        def __enter__(self):
            raise RuntimeError("ddgs down")

    ddgs_module.DDGS = FailingDDGS
    assert "Search failed" in web_search.web_search("entropy")[0]["error"]
