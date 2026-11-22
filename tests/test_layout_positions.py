"""以最终 PDF 的实际文本验证私有布局接口契约及锚点重排。"""

from pathlib import Path

import pypdfium2 as pdfium  # type: ignore[import-untyped]
import pytest

from weread_tui.graphics_render import RenderSession
from weread_tui.source_positions import prepare_document


@pytest.mark.parametrize("font_size", [16, 20, 28])
@pytest.mark.parametrize("line_height", [1.2, 1.6, 2.0])
async def test_long_paragraph_anchor_matches_actual_pdf_page(tmp_path: Path, font_size: int, line_height: float) -> None:
    """tmp_path 隔离会话；font_size/line_height 变化后锚点仍位于包含它的真实 PDF 页。"""
    before = "重复句中文 English 与组合 e\u0301、emoji 😀👩‍💻。" * 35
    after = "这是同段后面的重复句，重复句。" * 35
    source = f"<p>{before}锚{after}</p><p>下一段<em>强调 &amp; 实体 &#x1f600;</em></p>"
    prepared = prepare_document(source)
    anchor = prepared.anchor_from_source(source.index("锚"))
    session = RenderSession(tmp_path)
    try:
        count = await session.paginate(prepared, stylesheet="", images={}, width=320, height=400,
                                       font_size=font_size, line_height=line_height)
        assert count >= 3
        assert session.page_map is not None
        mapped_page = session.page_map.locate(anchor)
        actual_pages = []
        with pdfium.PdfDocument(session.directory / "chapter.pdf") as document:
            for index in range(len(document)):
                page = document[index]
                text = page.get_textpage()
                try:
                    if "锚" in text.get_text_range():
                        actual_pages.append(index)
                finally:
                    text.close()
                    page.close()
        assert actual_pages == [mapped_page]
        assert prepared.source_offset(anchor) == source.index("锚")
    finally:
        await session.close()


async def test_preserved_txt_spaces_remain_addressable(tmp_path: Path) -> None:
    """tmp_path 隔离排版；pre-wrap 的行首空格不被误当作已折叠空白。"""
    prepared = prepare_document("  A\n  B", "txt")
    anchor = prepared.anchor_from_source(0)
    assert anchor.text_offset == 0
    assert prepared.source_offset(anchor) == 0
    session = RenderSession(tmp_path)
    try:
        await session.paginate(prepared, stylesheet="", images={}, width=320, height=400)
        assert session.page_map is not None
        assert session.page_map.locate(anchor) == 0
    finally:
        await session.close()
