"""
Web Search - DuckDuckGo-backed search for the Study TUI agent.
No API key required. Returns titles, URLs, and content.
Fetches full page text for top results so the agent gets real substance.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request


_ALLOWED_SCHEMES = {"https"}
_MAX_RESULTS = 10
_MAX_FETCH_BYTES = 100_000


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse redirects so validation happens before any follow-up request."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def _is_public_hostname(hostname: str) -> bool:
    host = hostname.strip().lower().rstrip(".")
    if not host or host == "localhost":
        return False

    try:
        addrs = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False

    for _, _, _, _, sockaddr in addrs:
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False
    return True


def _is_safe_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False

    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return False
    if parsed.username or parsed.password:
        return False
    if not parsed.hostname:
        return False
    return _is_public_hostname(parsed.hostname)


def _strip_html(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for ent, ch in [
        ("&amp;", "&"),
        ("&lt;", "<"),
        ("&gt;", ">"),
        ("&quot;", '"'),
        ("&#39;", "'"),
        ("&nbsp;", " "),
    ]:
        text = text.replace(ent, ch)
    return text


def _fetch_page_text(url: str, max_chars: int = 3000) -> str:
    """Fetch a URL and return cleaned text content, truncated."""
    if not _is_safe_url(url):
        return ""

    opener = urllib.request.build_opener(_NoRedirectHandler)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})  # noqa: S310

    try:
        with opener.open(req, timeout=5) as resp:  # noqa: S310
            final_url = resp.geturl()
            if not _is_safe_url(final_url):
                return ""

            content_type = (resp.headers.get("Content-Type") or "").lower()
            if "text/html" not in content_type and "text/plain" not in content_type:
                return ""

            raw = resp.read(_MAX_FETCH_BYTES).decode("utf-8", errors="ignore")
        text = _strip_html(raw)
        if len(text) < 100:
            return ""
        return text[:max_chars]
    except urllib.error.HTTPError as exc:
        if 300 <= getattr(exc, "code", 0) < 400:
            return ""
        return ""
    except Exception:
        return ""


def web_search(query: str, max_results: int = 5, fetch_pages: bool = True) -> list[dict]:
    """Search the web via DuckDuckGo."""
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            return [{"error": "No search package found. Install with: pip install ddgs"}]

    try:
        try:
            max_results = int(max_results)
        except (TypeError, ValueError):
            max_results = 5
        max_results = max(1, min(max_results, _MAX_RESULTS))

        results = []
        with DDGS() as ddgs:
            for result in ddgs.text(query, max_results=max_results):
                url = result.get("href", "")
                if not _is_safe_url(url):
                    continue
                results.append(
                    {
                        "title": result.get("title", ""),
                        "url": url,
                        "snippet": result.get("body", ""),
                    }
                )

        if not results:
            return [{"info": f"No results found for: {query}"}]

        if fetch_pages:
            for result in results[:3]:
                content = _fetch_page_text(result["url"])
                if content:
                    result["content"] = content

        return results
    except Exception as exc:
        return [{"error": f"Search failed: {exc}"}]
