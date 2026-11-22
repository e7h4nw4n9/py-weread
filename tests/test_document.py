"""通过解析结果验证图文顺序与脚本隔离。"""

from weread_tui.document import ImageReference, parse_content
from weread_tui.models import Book, Chapter, ChapterDocument


def test_image_stays_between_its_surrounding_paragraphs() -> None:
    """显示转换保持图文顺序，不显示或执行脚本。"""
    document = ChapterDocument(Book("reader", "book", "书"), Chapter("chapter", "章"),
        '<p>图片之前</p><img src="Images/pic" alt="示例图"><script>secret()</script><p>图片之后</p>', "epub")
    assert parse_content(document) == ["图片之前", ImageReference("Images/pic", "示例图"), "图片之后"]


def test_txt_does_not_interpret_angle_brackets_as_images() -> None:
    """TXT 中的 HTML 样例仍作为普通正文，不产生网络资源。"""
    document = ChapterDocument(Book("reader", "book", "书"), Chapter("chapter", "章"), '<img src="x">', "txt")
    assert parse_content(document) == ['<img src="x">']


def test_svg_image_reference_is_not_silently_dropped() -> None:
    """SVG 中的图片引用也进入资源处理，加载失败时能显示缺图提示。"""
    document = ChapterDocument(Book("reader", "book", "书"), Chapter("chapter", "章"),
        '<svg><image xlink:href="Images/picture"/></svg>', "epub")
    assert parse_content(document) == [ImageReference("Images/picture")]
