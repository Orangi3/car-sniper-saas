"""
tests/test_console_escaping.py — the owner console renders third-party
listing text (marketplace titles are written by sellers). Every such value
must pass through H() / U(); a raw interpolation is a stored-XSS hole in
the admin session.
"""
import re
from pathlib import Path

PAGE = (Path(__file__).resolve().parent.parent / "dashboard.html").read_text()


def test_helpers_exist():
    assert "const H = (s) =>" in PAGE and "const U = (u) =>" in PAGE
    assert 'x.protocol === "https:" || x.protocol === "http:"' in PAGE


def test_no_raw_listing_fields_in_templates():
    # `${r.note ? ... : ''}` (a condition) is fine; a value is not
    raw = re.findall(r'\$\{\s*\(?\s*[a-z]\.(title|url|make|model|source|note|description|location)'
                     r'\b(?!\s*\?)', PAGE)
    assert raw == [], f"unescaped listing fields: {raw}"
    assert "'+r.state+'" not in PAGE
