from ideafinder.collectors.text import detect_language, html_to_text, is_kept_language
from tests.collectors.fakes import SPANISH


def test_html_to_text_keeps_paragraphs_and_decodes_entities():
    assert html_to_text("<p>One &amp; two</p><p>Three&#x27;s</p>") == "One & two\n\nThree's"
    assert html_to_text(None) == ""


def test_detects_english_and_other_languages():
    english = "We spend hours every week copying invoices from email into our accounting system."
    assert detect_language(english) == "en"
    assert detect_language(SPANISH) == "es"


def test_short_or_link_only_text_is_undetermined_and_kept():
    assert detect_language("+1") == "und"
    assert detect_language("https://getsupport.atlassian.com/browse/JST-955717") == "und"
    assert is_kept_language("und") and is_kept_language("en") and not is_kept_language("es")
