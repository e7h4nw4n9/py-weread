"""阅读领域的书架、章节与原始正文。"""

from dataclasses import dataclass, field
from hashlib import sha256


@dataclass(frozen=True)
class Book:
    """账号内的条目；book_id 是源标识，kind 区分非电子书内容。"""

    account_id: str
    book_id: str
    title: str
    author: str = ""
    kind: str = "book"


@dataclass(frozen=True)
class Chapter:
    """章节 uid 是稳定身份，level 仅决定目录层级。"""

    uid: str
    title: str
    level: int = 1


@dataclass(frozen=True)
class ChapterDocument:
    """保留 book/chapter 的 source/format；from_cache 只标记来源，不改变内容身份。"""

    book: Book
    chapter: Chapter
    source: str
    format: str
    from_cache: bool = field(default=False, compare=False)

    @property
    def revision(self) -> str:
        """用原始正文字节标识内容版本。"""
        return sha256(self.source.encode()).hexdigest()
