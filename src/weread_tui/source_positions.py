"""规范化正文片段并保留实体、换行与原始字符坐标的映射。"""

from hashlib import sha256
from html import escape, unescape
from html.parser import HTMLParser

from weread_tui.positions import LAYOUT_WHITESPACE, PreparedDocument, SourceRun


def normalize_characters(characters: list[str], spans: list[tuple[int, int]]) -> SourceRun:
    """规范化 characters 及对应 spans，保留普通空白供 CSS 排版。"""
    text: list[str] = []
    positions: list[tuple[int, int]] = []
    index = 0
    while index < len(characters):
        character = characters[index]
        start, end = spans[index]
        if character == "\r":
            if index + 1 < len(characters) and characters[index + 1] == "\n":
                index += 1
                end = spans[index][1]
            character = "\n"
        if character not in ("\u00ad", "\ufeff"):
            text.append(character)
            positions.append((start, end))
        index += 1
    return SourceRun("".join(text), positions)


class SourceParser(HTMLParser):
    """按原始文本节点标记来源，不逐字插入元素，也不执行页面脚本。"""

    def __init__(self, source: str) -> None:
        """source 为未经显示转换的章节 XHTML。"""
        super().__init__(convert_charrefs=False)
        self.source = source
        self.lines = [0] + [index + 1 for index, character in enumerate(source) if character == "\n"]
        self.output: list[str] = []
        self.runs: dict[str, SourceRun] = {}
        self.characters: list[str] = []
        self.spans: list[tuple[int, int]] = []
        self.hidden = 0

    def current_source_offset(self) -> int:
        """返回当前回调在原始 Unicode 字符串中的位置。"""
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def flush_text(self) -> None:
        """将一个原始文本节点规范化为单一来源片段。"""
        run = normalize_characters(self.characters, self.spans)
        self.characters, self.spans = [], []
        if run.text and not run.text.strip(LAYOUT_WHITESPACE):
            # 表格等结构标签之间的空白不能包入自定义元素。
            self.output.append(escape(run.text, quote=False))
        elif run.text:
            run_id = f"t{len(self.runs)}"
            self.runs[run_id] = run
            self.output.append(f'<weread-text data-weread-run="{run_id}">{escape(run.text, quote=False)}</weread-text>')

    def handle_data(self, data: str) -> None:
        """data 是原始文本；隐藏范围保持原样但不产生阅读锚点。"""
        if self.hidden:
            self.output.append(data)
        else:
            start = self.current_source_offset()
            self.characters.extend(data)
            self.spans.extend((start + index, start + index + 1) for index in range(len(data)))

    def entity(self, prefix: str, name: str) -> None:
        """记录 prefix/name 组成的实体，展开字符共同指向原始实体范围。"""
        start = self.current_source_offset()
        end = start + len(prefix) + len(name)
        if self.source[end:end + 1] == ";":
            end += 1
        raw = self.source[start:end]
        if self.hidden:
            self.output.append(raw)
        else:
            value = unescape(raw)
            self.characters.extend(value)
            self.spans.extend([(start, end)] * len(value))

    def handle_entityref(self, name: str) -> None:
        """解码 name 对应的 HTML 命名实体。"""
        self.entity("&", name)

    def handle_charref(self, name: str) -> None:
        """解码 name 对应的十进制或十六进制字符实体。"""
        self.entity("&#", name)

    def marked_tag(self, tag: str) -> str:
        """保留 tag 的原始写法，仅给图片或 SVG 根节点附加来源标识。"""
        raw = self.get_starttag_text()
        assert raw is not None
        if not self.hidden and tag in ("img", "svg"):
            run_id = f"i{len(self.runs)}"
            start = self.current_source_offset()
            self.runs[run_id] = SourceRun("\ufffc", [(start, start + len(raw))])
            end = raw.rfind("/>") if raw.endswith("/>") else len(raw) - 1
            raw = raw[:end] + f' data-weread-run="{run_id}"' + raw[end:]
        return raw

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """保留 tag/attrs，头部与脚本样式及 SVG 内部不改写文本。"""
        self.flush_text()
        self.output.append(self.marked_tag(tag))
        if tag in ("head", "script", "style", "svg"):
            self.hidden += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """保留 tag/attrs 的自闭合写法。"""
        self.flush_text()
        self.output.append(self.marked_tag(tag))

    def handle_endtag(self, tag: str) -> None:
        """结束 tag 的作用范围，保留原始结束标签。"""
        self.flush_text()
        start = self.current_source_offset()
        self.output.append(self.source[start:self.source.index(">", start) + 1])
        if tag in ("head", "script", "style", "svg"):
            self.hidden = max(0, self.hidden - 1)

    def handle_comment(self, data: str) -> None:
        """保留注释 data，不将其计入可见片段。"""
        self.flush_text()
        self.output.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        """保留文档声明 decl。"""
        self.flush_text()
        self.output.append(f"<!{decl}>")

    def handle_pi(self, data: str) -> None:
        """保留 XML 处理声明 data，不执行。"""
        self.flush_text()
        self.output.append(f"<?{data}>")


def prepare_document(source: str, format_name: str = "epub") -> PreparedDocument:
    """根据 format_name 为 source 建立稳定片段和原始字符坐标。"""
    revision = sha256(source.encode()).hexdigest()
    if format_name == "txt":
        run = normalize_characters(list(source), [(index, index + 1) for index in range(len(source))])
        html = f'<html><body><div style="white-space:pre-wrap"><weread-text data-weread-run="t0">{escape(run.text)}</weread-text></div></body></html>'
        return PreparedDocument(html, {"t0": run}, revision, len(source))
    parser = SourceParser(source)
    parser.feed(source)
    parser.close()
    parser.flush_text()
    return PreparedDocument("".join(parser.output), parser.runs, revision, len(source))
