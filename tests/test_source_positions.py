"""验证源字符坐标与独立于排版的正文片段锚点。"""

from weread_tui.source_positions import prepare_document


def test_entities_and_unicode_keep_raw_source_coordinates() -> None:
    """实体展开按 Unicode 码点索引，同时保留其原始源范围。"""
    prepared = prepare_document("<p>甲&amp;乙😀</p>")
    run = next(iter(prepared.runs.values()))
    assert run.text == "甲&乙😀"
    assert run.source_spans == [(3, 4), (4, 9), (9, 10), (10, 11)]
    anchor = prepared.anchor_from_source(6)
    assert anchor.text_offset == 1
    assert prepared.source_offset(anchor) == 4


def test_inline_boundaries_create_runs_without_splitting_each_character() -> None:
    """行内强调边界可分片，长段不逐字添加元素。"""
    prepared = prepare_document("<p>甲乙<em>丙丁</em>戊己</p>")
    assert [run.text for run in prepared.runs.values()] == ["甲乙", "丙丁", "戊己"]
    assert prepared.html.count("data-weread-run=") == 3


def test_normalization_removes_soft_hyphen_and_unifies_line_endings() -> None:
    """规范化后的索引仍可追溯到原始软连字符和 CRLF 周围的位置。"""
    prepared = prepare_document("a\u00adb\r\nc", format_name="txt")
    run = next(iter(prepared.runs.values()))
    assert run.text == "ab\nc"
    assert run.source_spans == [(0, 1), (2, 3), (3, 5), (5, 6)]


def test_revision_changes_and_missing_characters_fail_explicitly() -> None:
    """跨正文版本或丢失的字符没有可猜测的替代页码。"""
    import pytest
    from weread_tui.positions import PageMap, PageSpan, PositionError

    prepared = prepare_document("<p>甲乙</p>")
    anchor = prepared.anchor_from_source(4)
    mapping = PageMap(prepared.revision, [PageSpan("t0", 0, 1, 0)])
    with pytest.raises(PositionError, match="无法定位"):
        mapping.locate(anchor)
    changed = prepare_document("<p>甲丙</p>")
    with pytest.raises(PositionError, match="版本已变化"):
        changed.source_offset(anchor)


def test_table_whitespace_keeps_original_structure() -> None:
    """结构标签之间的空白不产生非法行内包装元素。"""
    prepared = prepare_document('<table>\n<tr><td>内容</td></tr>\n</table>')
    assert '<table>\n<tr>' in prepared.html
    assert '</tr>\n</table>' in prepared.html
    assert [run.text for run in prepared.runs.values()] == ["内容"]
