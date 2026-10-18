"""The official page's text (`watch/article_text.py`): UC3's bounded fetch.

No network: the HTML shapes below are the three sites' own, as measured on
2026-10-18 — the Fed marks its content `role="main"` on a div, the ECB and the
SNB use `<main>` — and the domain bound is checked before any request leaves.
"""

import aiohttp
import pytest

from investment.watch import article_text as AT

DOMAINS = ("federalreserve.gov", "ecb.europa.eu", "snb.ch")

FED_PAGE = """<html><head><title>Federal Reserve issues FOMC statement</title>
<script>var tracking = 1;</script><style>.x{}</style></head><body>
<nav><a href="/">Home</a><a href="/careers">Careers</a></nav>
<div id="content" class="container" role="main">
  <div><h3>Federal Reserve issues FOMC statement</h3>
  <p>The Committee decided to raise the target range &amp; to hold reserves ample.
  <p>Inflation remains elevated.</p></div>
</div>
<footer>Last Update: September 16, 2026</footer></body></html>"""


def test_the_main_region_is_kept_and_the_navigation_dropped() -> None:
    text = AT.html_text(FED_PAGE)
    assert "raise the target range & to hold reserves ample." in text
    assert "Inflation remains elevated." in text
    # The unclosed <p> did not swallow the rest, and nothing around main leaked.
    for noise in ("Careers", "tracking", ".x{}", "Last Update"):
        assert noise not in text


def test_a_page_with_no_main_region_gives_its_whole_visible_text() -> None:
    text = AT.html_text("<body><nav>Menu</nav><p>Statement   text.</p><script>x()</script></body>")
    assert text == "Statement text."


def test_only_a_pdf_on_a_watched_domain_is_followed() -> None:
    """The SNB's speeches are slides behind a link. The one hop stays on the
    pinned domains, as UC3 bounds it."""
    page = (
        '<a href="https://evil.example/x.pdf">x</a>'
        '<a href="/public/asset/en/ref_20261015_gpe.en.pdf">Download file now</a>'
    )
    url = "https://www.snb.ch/en/publications/communication/speeches/2026/ref_20261015_gpe"
    assert AT.pdf_link(page, url, DOMAINS) == (
        "https://www.snb.ch/public/asset/en/ref_20261015_gpe.en.pdf"
    )
    assert AT.pdf_link('<a href="https://evil.example/x.pdf">x</a>', url, DOMAINS) is None


def test_a_subdomain_counts_and_a_lookalike_does_not() -> None:
    assert AT.on_domains("https://www.ecb.europa.eu//press/x.html", DOMAINS)
    assert AT.on_domains("https://snb.ch/en", DOMAINS)
    assert not AT.on_domains("https://notsnb.ch/en", DOMAINS)
    assert not AT.on_domains("https://snb.ch.evil.example/en", DOMAINS)


def test_the_text_handed_on_is_capped_and_says_so() -> None:
    long = "a" * (AT.MAX_ARTICLE_CHARS + 10)
    assert AT.capped(long).endswith(AT.TRUNCATION_MARK)
    assert len(AT.capped(long)) == AT.MAX_ARTICLE_CHARS + len(AT.TRUNCATION_MARK)
    assert AT.capped("short") == "short"


async def test_a_url_off_the_watched_domains_is_refused_before_any_request() -> None:
    async with aiohttp.ClientSession() as session:
        with pytest.raises(AT.ArticleUnavailableError):
            await AT.fetch_article(session, "https://example.com/press.html", DOMAINS)
