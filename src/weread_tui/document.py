"""把原始章节转换为按出现顺序排列的文字与图片引用。"""

from dataclasses import dataclass
from html.parser import HTMLParser

from weread_tui.models import ChapterDocument


@dataclass(frozen=True)
class ImageReference:
    """src 是正文原始引用，alt 为缺图时显示的说明。"""

    src: str
    alt: str = "插图"


class DocumentParser(HTMLParser):
    """解析展示内容，不执行脚本或获取资源，不修改原始正文。"""

    def __init__(self) -> None:
        """初始化文字缓冲区、顺序内容与隐藏标签计数。"""
        super().__init__(convert_charrefs=True)
        self.blocks: list[str | ImageReference] = []
        self.text: list[str] = []
        self.hidden = 0

    def flush_text(self) -> None:
        """图片前或文档结束时提交已有文字。"""
        text = "".join(self.text).strip()
        if text:
            self.blocks.append(text)
        self.text = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """依据 tag 和 attrs 保留文字与图片顺序。"""
        if tag in ("head", "script", "style"):
            self.hidden += 1
        if self.hidden:
            return
        if tag in ("p", "div", "h1", "h2", "h3", "li", "blockquote", "br"):
            self.text.append("\n")
        elif tag in ("img", "image"):
            self.flush_text()
            attributes = dict(attrs)
            reference = attributes.get("src") or attributes.get("href") or attributes.get("xlink:href") or ""
            self.blocks.append(ImageReference(reference, attributes.get("alt") or "插图"))

    def handle_endtag(self, tag: str) -> None:
        """依据 tag 结束隐藏范围或段落。"""
        if tag in ("head", "script", "style"):
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden and tag in ("p", "div", "h1", "h2", "h3", "li", "blockquote"):
            self.text.append("\n")

    def handle_data(self, data: str) -> None:
        """仅收集可见范围中的 data。"""
        if not self.hidden:
            self.text.append(data)


def parse_content(document: ChapterDocument) -> list[str | ImageReference]:
    """按 document 的正文格式生成展示块，TXT 不解释为 HTML。"""
    if document.format == "txt":
        return [document.source]
    parser = DocumentParser()
    parser.feed(document.source)
    parser.close()
    parser.flush_text()
    return parser.blocks
