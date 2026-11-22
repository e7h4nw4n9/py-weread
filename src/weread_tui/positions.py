"""阅读锚点与当前分页结果，统一使用 Unicode 码点索引。"""

from dataclasses import dataclass

LAYOUT_WHITESPACE = " \t\r\n\f"


class PositionError(ValueError):
    """正文版本或布局无法准确定位，调用者不得猜测替代位置。"""


@dataclass(frozen=True)
class ContentAnchor:
    """run_id 标识正文片段，text_offset 为其字符偏移，revision 为原文版本。"""

    run_id: str
    text_offset: int
    revision: str


@dataclass(frozen=True)
class SourceRun:
    """text 与原始 source_spans 对应；图片使用一个对象占位字符。"""

    text: str
    source_spans: list[tuple[int, int]]


@dataclass(frozen=True)
class PreparedDocument:
    """html 为带来源标记的排版输入，runs 保留原文与规范化文本的关联。"""

    html: str
    runs: dict[str, SourceRun]
    revision: str
    source_length: int

    def anchor_from_source(self, offset: int) -> ContentAnchor:
        """把原始 offset 转为内容锚点，标签位置接续后面的字符。"""
        if not 0 <= offset <= self.source_length:
            raise PositionError("源位置超出当前章节范围。")
        following: ContentAnchor | None = None
        last: ContentAnchor | None = None
        for run_id, run in self.runs.items():
            for index, (start, end) in enumerate(run.source_spans):
                anchor = ContentAnchor(run_id, index, self.revision)
                if start <= offset < end:
                    return anchor
                if following is None and start >= offset:
                    following = anchor
                last = anchor
        result = following if following is not None else last
        if result is None:
            raise PositionError("当前章节没有可定位的可见内容。")
        return result

    def source_offset(self, anchor: ContentAnchor) -> int:
        """把 anchor 转回原始正文坐标，不将实体展开误当成原文长度。"""
        if anchor.revision != self.revision:
            raise PositionError("正文版本已变化，旧锚点不能直接复用。")
        run = self.runs.get(anchor.run_id)
        if run is None or not 0 <= anchor.text_offset < len(run.source_spans):
            raise PositionError("阅读锚点不属于当前章节。")
        return run.source_spans[anchor.text_offset][0]


@dataclass(frozen=True)
class PageSpan:
    """run_id 的 start/end 字符区间实际出现在 page 页。"""

    run_id: str
    start: int
    end: int
    page: int


@dataclass(frozen=True)
class PageMap:
    """仅保存当前排版的字符区间，不把页码当作持久化进度。"""

    revision: str
    spans: list[PageSpan]

    def locate(self, anchor: ContentAnchor) -> int:
        """返回实际包含 anchor 的页面；无法匹配时明确失败。"""
        if anchor.revision != self.revision:
            raise PositionError("正文版本已变化，无法沿用旧阅读位置。")
        for span in self.spans:
            if span.run_id == anchor.run_id and span.start <= anchor.text_offset < span.end:
                return span.page
        raise PositionError("当前排版无法定位原字符，未退回章节或段落开头。")

    def first_anchor(self, page: int) -> ContentAnchor | None:
        """优先以 page 内首段文字定位，纯图片页使用图片对象锚点。"""
        candidates = [span for span in self.spans if span.page == page]
        if not candidates:
            return None
        first = next((span for span in candidates if not span.run_id.startswith("i")), candidates[0])
        return ContentAnchor(first.run_id, first.start, self.revision)
