"""云端位置及实际阅读时间选择，不把请求时间冒充阅读时间。"""

from dataclasses import dataclass
from math import isfinite
import re
from typing import Literal

from weread_tui.progress import ReadingProgress
from weread_tui.models import ChapterDocument
from weread_tui.positions import ContentAnchor, PositionError
from weread_tui.source_positions import prepare_document


@dataclass(frozen=True)
class CloudProgress:
    """账号、书籍和章节标识位置；chapter_offset 为待转换的云端坐标，updated_at 为 Unix 秒。"""

    account_id: str
    book_id: str
    chapter_uid: str
    chapter_offset: int
    updated_at: float | None


def choose_progress(local: ReadingProgress | None, cloud: CloudProgress | None,
                    *, now: float) -> Literal["local", "cloud"] | None:
    """比较 local/cloud 的实际阅读时间；now 仅用于识别未来时间，不写入记录。"""
    if cloud is None:
        return "local" if local is not None else None
    if local is None:
        return "cloud"
    cloud_time = cloud.updated_at
    if cloud_time is None:
        return "local"
    for stamp in (local.updated_at, cloud_time):
        if not isfinite(stamp) or not 0 < stamp <= now:
            return "local"
    return "cloud" if cloud_time > local.updated_at else "local"


def cloud_anchor(document: ChapterDocument, position: CloudProgress) -> ContentAnchor:
    """将 position 的完整原文 UTF-16 坐标转换为 document 的本地字符锚点。"""
    if (position.account_id, position.book_id, position.chapter_uid) != (
        document.book.account_id, document.book.book_id, document.chapter.uid,
    ):
        raise PositionError("云端位置不属于当前账号、书籍或章节。")
    raw = document.source.encode("utf-16-le")
    end = position.chapter_offset * 2
    if not 0 <= end <= len(raw):
        raise PositionError("云端偏移超出当前章节，未近似恢复。")
    try:
        offset = len(raw[:end].decode("utf-16-le"))
    except UnicodeDecodeError as error:
        raise PositionError("云端偏移位于 UTF-16 字符中间，无法准确恢复。") from error
    if document.format == "epub" and offset:
        bodies = list(re.finditer(r"<body\b[^>]*>.*?</body\s*>", document.source, re.IGNORECASE | re.DOTALL))
        if bodies and not any(body.start() <= offset < body.end() for body in bodies):
            raise PositionError("云端偏移不在章节正文中，未用附近文字冒充恢复。")
    return prepare_document(document.source, document.format).anchor_from_source(offset)
