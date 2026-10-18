"""The text of a central-bank press item, fetched from its official page —
UC3's "bounded fetch restricted to the EVENT_SOURCES domains".

WHY IT EXISTS (owner, 2026-10-18: "ne me pose plus ces questions … ce tri doit
être automatique et pertinent"). The ECB and SNB feeds carry a title and
nothing else, and the Fed's a ninety-character summary, so the triage read
headlines. Its honesty rule then refused to describe what it could not see and
asked the owner instead, over a channel that was off: 11 of the first 23 items
it judged major never reached the corpus — the Fed's and the ECB's September
rate hikes among them. The text was one HTTP request away on the same domain.

BOUNDED, as UC3 asks: the URL must sit on one of the pinned domains (a redirect
off them is refused too), the download is capped, and the text handed on is
capped. A page that only links its content as a PDF (the SNB's speeches are
slides behind "Download file now") is followed ONE hop, to a PDF on the same
domain, and only when the page itself is thin.

NO NEW DEPENDENCY: `html.parser` from the standard library and `pypdf`, which
the corpus ingester already uses. Measured on the three sites (2026-10-18): the
Fed marks its content `role="main"`, the ECB and the SNB use `<main>`, so the
main region is what is kept, and the navigation around it is dropped.
"""

import asyncio
import io
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import aiohttp

ARTICLE_TIMEOUT_SECONDS = 30.0
# An ECB slide deck ran to 1.1 MB; a page past this is not a press item.
MAX_ARTICLE_BYTES = 8_000_000
# What reaches the model and the corpus: an ECB press conference with its Q&A
# is ~34,000 characters, and the decision is in its first paragraphs.
MAX_ARTICLE_CHARS = 20_000
# Under this, an HTML page is a cover for a linked file (the SNB's speeches).
THIN_PAGE_CHARS = 1_000

TRUNCATION_MARK = "\n[… truncated]"

_SKIPPED = frozenset(
    {"script", "style", "noscript", "svg", "nav", "header", "footer", "form", "aside", "button"}
)
_BLOCKS = frozenset(
    {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "main"}
    | {"table", "ul", "ol", "dd", "dt", "blockquote"}
)
_VOID = frozenset(
    {"br", "img", "hr", "meta", "link", "input", "source", "area", "base", "col", "embed", "wbr"}
)


class ArticleUnavailableError(Exception):
    """The page could not be fetched, or lies outside the pinned domains."""


class _PageText(HTMLParser):
    """The visible text of a page, with the text of its MAIN region kept apart."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        # (tag, skipped, main) for every open element: HTML leaves <p> and <li>
        # unclosed, so a closing tag pops back to its own opening one.
        self._open: list[tuple[str, bool, bool]] = []
        self.everything: list[str] = []
        self.main: list[str] = []
        self.has_main = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _VOID:
            if tag == "br":
                self._put("\n")
            return
        is_main = tag in ("main", "article") or dict(attrs).get("role") == "main"
        self.has_main = self.has_main or is_main
        self._open.append((tag, tag in _SKIPPED, is_main))
        if tag in _BLOCKS:
            self._put("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCKS:
            self._put("\n")
        if any(name == tag for name, _, _ in self._open):
            while self._open and self._open.pop()[0] != tag:
                pass

    def handle_data(self, data: str) -> None:
        if not any(skipped for _, skipped, _ in self._open):
            self._put(data)

    def _put(self, text: str) -> None:
        self.everything.append(text)
        if any(main for _, _, main in self._open):
            self.main.append(text)


def tidy(text: str) -> str:
    """Spaces collapsed, at most one blank line between paragraphs."""
    text = re.sub("[ \t\r\f\v\u00a0\ufeff]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def html_text(html: str) -> str:
    """The page's main region as text, or the whole page when it marks none."""
    parser = _PageText()
    parser.feed(html)
    parser.close()
    main = tidy("".join(parser.main)) if parser.has_main else ""
    return main or tidy("".join(parser.everything))


def pdf_text(raw: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(raw))
    return tidy("\n".join(page.extract_text() or "" for page in reader.pages))


def pdf_link(html: str, page_url: str, domains: tuple[str, ...]) -> str | None:
    """The first link to a PDF on a pinned domain, made absolute."""
    for href in re.findall(r'href="([^"]+\.pdf)(?:[?#][^"]*)?"', html, flags=re.IGNORECASE):
        url = urljoin(page_url, str(href))
        if on_domains(url, domains):
            return url
    return None


def on_domains(url: str, domains: tuple[str, ...]) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def capped(text: str) -> str:
    if len(text) <= MAX_ARTICLE_CHARS:
        return text
    return text[:MAX_ARTICLE_CHARS] + TRUNCATION_MARK


async def _download(
    session: aiohttp.ClientSession, url: str, domains: tuple[str, ...]
) -> tuple[bytes, bool]:
    """`(body, is_pdf)`; refuses a URL — or a redirect — off the pinned domains."""
    if not on_domains(url, domains):
        raise ArticleUnavailableError(f"{url} is outside the watched domains")
    async with session.get(
        url, timeout=aiohttp.ClientTimeout(total=ARTICLE_TIMEOUT_SECONDS)
    ) as response:
        response.raise_for_status()
        if not on_domains(str(response.url), domains):
            raise ArticleUnavailableError(f"{url} redirected off the watched domains")
        # Chunk by chunk: `content.read(n)` returns what has ARRIVED, up to n,
        # and handed the parser a third of a page on the first live run.
        body = bytearray()
        async for chunk in response.content.iter_chunked(64 * 1024):
            body.extend(chunk)
            if len(body) >= MAX_ARTICLE_BYTES:
                break
        raw = bytes(body[:MAX_ARTICLE_BYTES])
        is_pdf = "pdf" in response.headers.get("Content-Type", "") or raw[:4] == b"%PDF"
    return raw, is_pdf


async def fetch_article(session: aiohttp.ClientSession, url: str, domains: tuple[str, ...]) -> str:
    """The item's text from its official page, capped at `MAX_ARTICLE_CHARS`.
    Empty when the page carries no text; raises `ArticleUnavailableError` (or
    the transport's error) when it cannot be fetched at all."""
    raw, is_pdf = await _download(session, url, domains)
    if is_pdf:
        # CPU-bound on a slide deck, so off the event loop.
        return capped(await asyncio.to_thread(pdf_text, raw))
    html = raw.decode("utf-8", errors="replace")
    text = html_text(html)
    if len(text) < THIN_PAGE_CHARS and (linked := pdf_link(html, url, domains)):
        linked_raw, _ = await _download(session, linked, domains)
        linked_text = await asyncio.to_thread(pdf_text, linked_raw)
        if len(linked_text) > len(text):
            text = linked_text
    return capped(text)
