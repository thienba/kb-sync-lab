import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup, Comment, Tag
from markdownify import MarkdownConverter

from kbsync.zendesk import Article

# Bump whenever the conversion logic changes; it is not part of the content hash.
CONVERTER_VERSION = "2"

BASE_URL = "https://support.optisigns.com/"
SLUG_MAX = 80
_HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden"
    r"|opacity\s*:\s*0(?:\.0*)?\s*(?:[;!]|$)"
    r"|font-size\s*:\s*0+(?:\.0*)?[a-z%]*\s*(?:[;!]|$)",
    re.I,
)
_OFFSCREEN = re.compile(r"(?:^|[;\s])(?:left|text-indent)\s*:\s*-\s*(\d+(?:\.\d+)?)", re.I)
_LINK_SCHEMES = {"http", "https", "mailto", "tel"}
_INVISIBLE_CHARS = re.compile("[​‌‍⁠﻿]")
_EMPTY_BOLD = re.compile(r"(\*\*|__)\s*\1")
_FILENAME_ALT = re.compile(r".+\.(?:jpe?g|png|gif|webp|svg|bmp)", re.I)
_BLANK_RUN = re.compile(r"\n{3,}")
_VIDEO_HOSTS = ("youtube.com", "youtube-nocookie.com", "vimeo.com", "wistia.com", "loom.com")
_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}


@dataclass(frozen=True)
class ConvertedArticle:
    article_id: int
    title: str
    url: str
    updated_at: str
    filename: str
    markdown: str
    body_hash: str


def _absolute(url: str) -> str:
    return urljoin(BASE_URL, url.strip())


def _video_link(src: str) -> str:
    """Normalise a YouTube embed URL to a watch URL; other embeds keep their own URL."""
    url = _absolute(src)
    parsed = urlparse(url)
    if parsed.hostname in _YOUTUBE_HOSTS:
        if parsed.path.startswith("/embed/"):
            return f"https://www.youtube.com/watch?v={parsed.path.split('/')[2]}"
        if parsed.path == "/watch" and "v" in parse_qs(parsed.query):
            return f"https://www.youtube.com/watch?v={parse_qs(parsed.query)['v'][0]}"
    return url


def _is_video(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return any(host.endswith(name) for name in _VIDEO_HOSTS)


def _is_hidden(tag: Tag) -> bool:
    style = tag.get("style") or ""
    return (
        tag.has_attr("hidden")
        or tag.get("aria-hidden", "").strip().lower() == "true"
        or bool(_HIDDEN_STYLE.search(style))
        or any(float(offset) >= 999 for offset in _OFFSCREEN.findall(style))
    )


def _callout(table: Tag, soup: BeautifulSoup) -> Tag:
    """A one-column table is a NOTE/IMPORTANT box: turn it into a blockquote."""
    quote = soup.new_tag("blockquote")
    for cell in table.find_all("td"):
        if cell.find("p"):
            for child in list(cell.children):
                quote.append(child.extract())
        else:
            para = soup.new_tag("p")
            for child in list(cell.children):
                para.append(child.extract())
            quote.append(para)
    return quote


def _sanitise(html: str, page_url: str) -> BeautifulSoup:
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.find_all(string=lambda s: isinstance(s, Comment)):
        node.extract()
    for text in soup.find_all(string=True):
        if "\xa0" in text:
            text.replace_with(text.replace("\xa0", " "))
    for tag in soup.find_all(["script", "style", "noscript", "template", "hr"]):
        tag.decompose()
    for tag in [t for t in soup.find_all(True) if _is_hidden(t)]:
        tag.decompose()
    for tag in soup.find_all(True):
        for attr in ("style", "id"):
            del tag[attr]
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        if not tag.get_text(strip=True):
            if tag.find("img"):
                tag.name = "p"  # an image alone is not a heading
            else:
                tag.decompose()
        else:
            for inner in tag.find_all(["strong", "b", "em", "i"]):
                inner.unwrap()
    for item in soup.find_all("li"):
        kids = [c for c in item.children if not (isinstance(c, str) and not c.strip())]
        if len(kids) == 1 and kids[0].name in ("ul", "ol"):
            parent_item = item.find_previous_sibling("li")
            if parent_item is not None:  # a nested list wrapped in its own empty li
                parent_item.append(kids[0].extract())
                item.decompose()
            else:
                kids[0].unwrap()
                item.unwrap()
    for toc in soup.find_all("ul"):
        links = toc.find_all("a")
        if (
            links
            and all(a.get("href", "").startswith("#") for a in links)
            and "".join(toc.get_text().split())
            == "".join("".join(a.get_text().split()) for a in links)
        ):
            toc.decompose()
    for tag in soup.find_all(["em", "i"]):
        if tag.find(["strong", "b"]):
            tag.unwrap()
    for tag in soup.find_all(["strong", "b"]):
        if not tag.get_text(strip=True):
            tag.unwrap()
    for tag in soup.find_all("a"):
        href = tag.get("href")
        if href is None or not tag.get_text(strip=True) and not tag.find("img"):
            tag.unwrap()
        elif href.startswith("#"):
            if len(href) > 1:  # heading ids are stripped, so point at the live page
                tag["href"] = page_url + href
            else:
                tag.unwrap()
        elif urlparse(_absolute(href)).scheme in _LINK_SCHEMES:
            tag["href"] = _absolute(href)
        else:
            tag.unwrap()  # javascript:, data: and the like: keep the text, drop the link
    for tag in soup.find_all("img"):
        src = tag.get("src") or ""
        if src.startswith("data:"):  # inline base64 blob: megabytes of noise, not a link
            tag.decompose()
        elif src:
            tag["src"] = _absolute(src)
    for tag in soup.find_all("iframe"):
        link = soup.new_tag("p")
        anchor = soup.new_tag("a", href=_video_link(tag.get("src", "")))
        anchor.string = "Watch video" if _is_video(anchor["href"]) else "Embedded content"
        link.append(anchor)
        tag.replace_with(link)
    for figure in soup.find_all("figure"):
        figure.unwrap()
    for table in soup.find_all("table"):
        if (
            max(len(row.find_all(["td", "th"], recursive=False)) for row in table.find_all("tr"))
            < 2
        ):
            table.replace_with(_callout(table, soup))
            continue
        if not table.find("thead"):
            first = table.find("tr")
            head = soup.new_tag("thead")
            first.insert_before(head)
            head.append(first.extract())
            for cell in first.find_all("td", recursive=False):
                cell.name = "th"
        for cell in table.find_all(["td", "th"]):
            for br in cell.find_all("br"):
                br.replace_with(" ")
            for para in cell.find_all("p"):
                para.insert_after(" ")
                para.unwrap()
    return soup


class _Converter(MarkdownConverter):
    def convert_img(self, el, text, parent_tags):
        alt = " ".join((el.get("alt") or "").split())
        if _FILENAME_ALT.fullmatch(alt):
            alt = ""
        return f"![{alt}]({el.get('src', '')})"

    def convert_td(self, el, text, parent_tags):
        return super().convert_td(el, text.replace("|", "\\|"), parent_tags)

    convert_th = convert_td


def _language(el) -> str | None:
    for cls in el.find("code").get("class", []) if el.find("code") else []:
        if cls.startswith("language-") and cls != "language-auto":
            return cls.removeprefix("language-")
    return None


def _clean(markdown: str) -> str:
    text = unicodedata.normalize("NFC", markdown.replace("\xa0", " ").replace("&nbsp;", " "))
    text = _INVISIBLE_CHARS.sub("", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = _EMPTY_BOLD.sub("", text)
    text = re.sub(r"^#{1,6}$", "", text, flags=re.M)
    return _BLANK_RUN.sub("\n\n", text).strip()


def html_to_markdown(html: str, page_url: str) -> str:
    soup = _sanitise(html, page_url)
    markdown = _Converter(
        heading_style="ATX",
        bullets="-",
        escape_underscores=False,
        code_language_callback=_language,
        strong_em_symbol="*",
    ).convert_soup(soup)
    return _clean(markdown)


def slugify(title: str) -> str:
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_title.lower()).strip("-")[:SLUG_MAX].strip("-")
    return slug or "article"


def convert(article: Article) -> ConvertedArticle:
    title = " ".join(article.title.replace("\xa0", " ").split())
    body = html_to_markdown(article.body_html, article.html_url)
    parts = [f"# {title}", f"Article URL: {article.html_url}"]
    if body:
        parts.append(body)
    content = "\n\n".join(parts) + "\n"
    front_matter = "\n".join(
        [
            "---",
            f"title: {json.dumps(title, ensure_ascii=False)}",
            f"article_url: {json.dumps(article.html_url)}",
            f"article_id: {article.id}",
            f"updated_at: {json.dumps(article.updated_at)}",
            "---",
        ]
    )
    return ConvertedArticle(
        article_id=article.id,
        title=title,
        url=article.html_url,
        updated_at=article.updated_at,
        filename=f"{article.id}-{slugify(title)}.md",
        markdown=f"{front_matter}\n\n{content}",
        body_hash=hashlib.sha256(content.encode()).hexdigest(),
    )


def convert_all(articles: Iterable[Article]) -> tuple[list[ConvertedArticle], dict[int, str]]:
    """Convert each article; one that raises is reported in the failure map, not fatal."""
    converted: list[ConvertedArticle] = []
    failed: dict[int, str] = {}
    for article in articles:
        try:
            converted.append(convert(article))
        except Exception as exc:  # noqa: BLE001 - per-article isolation is the requirement
            failed[article.id] = f"{type(exc).__name__}: {exc}"
    return converted, failed


def write_all(converted: Iterable[ConvertedArticle], out_dir: Path) -> dict[int, str]:
    """Write each file under out_dir; return {article_id: reason} for those that failed."""
    out_dir.mkdir(parents=True, exist_ok=True)
    failed: dict[int, str] = {}
    for item in converted:
        try:
            (out_dir / item.filename).write_text(item.markdown, encoding="utf-8", newline="\n")
        except OSError as exc:
            failed[item.article_id] = f"{type(exc).__name__}: {exc}"
    return failed


def violations(markdown: str) -> list[str]:
    """Cleanliness rules every output file must satisfy; empty list means clean."""
    found = []
    if "\xa0" in markdown or "&nbsp;" in markdown:
        found.append("contains a non-breaking space")
    if "\n\n\n" in markdown:
        found.append("contains 3 or more consecutive newlines")
    if re.search(r"^#{1,6}\s*$", markdown, re.M):
        found.append("contains a heading with no text")
    if not markdown.endswith("\n") or markdown.endswith("\n\n"):
        found.append("does not end with exactly one newline")
    return found
