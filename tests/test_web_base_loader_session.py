"""Exercise the actual LangChain loader against a local, session-aware HTTP server."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from gpt_researcher.scraper.web_base_loader.web_base_loader import WebBaseLoaderScraper


@pytest.fixture
def local_page():
    """Serve different bodies when a request loses the caller's session configuration."""
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received = {
                "header": self.headers.get("X-Research-Token"),
                "cookie": self.headers.get("Cookie"),
                "user-agent": self.headers.get("User-Agent"),
            }
            seen.append(received)
            kind = self.path.lstrip("/")
            expected = {
                "header": "local-test-token",
                "cookie": "research_access=local-test-token",
                "user-agent": "LocalResearcher/1.0",
            }
            allowed = kind == "public" or received.get(kind) == expected.get(kind)
            title = "Research page" if allowed else "Sign in"
            text = (
                "The session-protected research body." if allowed else "Access denied."
            )
            body = (
                f"<html><head><title>{title}</title></head>"
                f"<body><p>{text}</p><img src='/figure.png' class='hero'></body></html>"
            ).encode()
            self.send_response(200 if allowed else 403)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("kind", ["header", "cookie", "user-agent"])
def test_content_and_metadata_use_the_supplied_session(local_page, kind):
    """Both real HTTP requests must carry the same caller-owned headers and cookies."""
    base, seen = local_page
    with requests.Session() as session:
        session.trust_env = False
        session.headers.update(
            {
                "X-Research-Token": "local-test-token",
                "User-Agent": "LocalResearcher/1.0",
            }
        )
        session.cookies.set("research_access", "local-test-token")

        content, images, title = WebBaseLoaderScraper(
            f"{base}/{kind}", session=session
        ).scrape()

        assert "The session-protected research body." in content
        assert "Access denied" not in content
        assert title == "Research page"
        assert images == [{"url": f"{base}/figure.png", "score": 4}]
        assert len(seen) == 2
        assert (
            seen[0]
            == seen[1]
            == {
                "header": "local-test-token",
                "cookie": "research_access=local-test-token",
                "user-agent": "LocalResearcher/1.0",
            }
        )


def test_public_page_without_an_explicit_session(local_page):
    """Default session construction still scrapes a normal public page."""
    base, seen = local_page
    scraper = WebBaseLoaderScraper(f"{base}/public")
    scraper.session.trust_env = False
    try:
        content, _, title = scraper.scrape()
    finally:
        scraper.session.close()
    assert "The session-protected research body." in content
    assert title == "Research page"
    assert len(seen) == 2
