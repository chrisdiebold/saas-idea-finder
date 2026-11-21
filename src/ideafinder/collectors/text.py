"""Turning Source content into plain text, and the English filter."""

import html
import re
from functools import lru_cache
from html.parser import HTMLParser

from lingua import Language, LanguageDetector, LanguageDetectorBuilder

_BLOCK_TAGS = {
    "p",
    "br",
    "div",
    "li",
    "ul",
    "ol",
    "pre",
    "blockquote",
    "h1",
    "h2",
    "h3",
    "h4",
    "tr",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(value: str | None) -> str:
    """Strip tags, decode entities, and normalise whitespace while keeping paragraph breaks."""
    if not value:
        return ""
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    text = "".join(parser.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def unescape(value: str | None) -> str | None:
    """Decode HTML entities in short fields (titles, names) that contain no markup."""
    return html.unescape(value) if value is not None else None


# Languages common on the Sources we collect from. Restricting the set makes detection faster
# and more accurate than considering all 75 languages lingua supports.
_LANGUAGES = [
    Language.ENGLISH,
    Language.SPANISH,
    Language.PORTUGUESE,
    Language.FRENCH,
    Language.GERMAN,
    Language.ITALIAN,
    Language.DUTCH,
    Language.POLISH,
    Language.RUSSIAN,
    Language.UKRAINIAN,
    Language.TURKISH,
    Language.ARABIC,
    Language.HINDI,
    Language.INDONESIAN,
    Language.VIETNAMESE,
    Language.CHINESE,
    Language.JAPANESE,
    Language.KOREAN,
    Language.PERSIAN,
    Language.SWEDISH,
]
_MIN_CHARS_TO_JUDGE = 40  # shorter texts can't be classified reliably, so they're kept


@lru_cache(maxsize=1)
def _detector() -> LanguageDetector:
    return LanguageDetectorBuilder.from_languages(*_LANGUAGES).build()


# Links and mentions say nothing about the language (a bare URL reads as Dutch to the detector).
_NOT_PROSE = re.compile(
    r"https?://\S+"  # URLs
    r"|\[[^\]|]*\|[^\]]*\]"  # Jira wiki links: [label|url]
    r"|(?<!\w)@[\w.-]+"  # @mentions
)


def detect_language(text: str) -> str:
    """ISO 639-1 code of the text's language, or "und" if it's too short or unclear."""
    sample = re.sub(r"\s+", " ", _NOT_PROSE.sub(" ", text)).strip()[:2000]
    if len(sample) < _MIN_CHARS_TO_JUDGE:
        return "und"
    language = _detector().detect_language_of(sample)
    return language.iso_code_639_1.name.lower() if language else "und"


def is_kept_language(code: str) -> bool:
    """English only (PLAN.md §1). Undetermined text is kept rather than lost."""
    return code in ("en", "und")
