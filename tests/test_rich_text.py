from backend.core.rich_text import rich_text_has_content, sanitize_rich_html
from backend.app_v2.runtime.security import _allow_query_token


def test_rich_text_keeps_board_images_and_tables():
    raw = (
        '<p><b>배정 결과</b></p>'
        '<img src="/api/lot-requests/files/abc123/paste.png?t=secret" onerror="alert(1)">'
        '<table style="border-collapse:collapse;position:fixed"><tbody>'
        '<tr><th>LOT</th><th>결과</th></tr><tr><td>L001</td><td>완료</td></tr>'
        '</tbody></table>'
    )
    cleaned = sanitize_rich_html(raw)
    assert "<table" in cleaned and "<th>LOT</th>" in cleaned
    assert 'src="/api/lot-requests/files/abc123/paste.png"' in cleaned
    assert "onerror" not in cleaned
    assert "position" not in cleaned
    assert rich_text_has_content(cleaned)


def test_rich_text_removes_scripts_and_foreign_images():
    cleaned = sanitize_rich_html(
        '<script>alert(1)</script><img src="https://evil.example/x.png"><p>안전</p>'
    )
    assert "script" not in cleaned
    assert "evil.example" not in cleaned
    assert "안전" in cleaned


def test_image_or_table_counts_as_content_but_empty_markup_does_not():
    assert not rich_text_has_content("<p><br></p>")
    assert rich_text_has_content('<img src="/api/informs/files/a/p.png">')
    assert rich_text_has_content("<table><tr><td></td></tr></table>")


def test_lot_request_images_allow_narrow_query_token_auth():
    assert _allow_query_token("/api/lot-requests/files/abc123/paste.png")
    assert not _allow_query_token("/api/lot-requests/upload")
