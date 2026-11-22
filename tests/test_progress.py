"""通过真实 SQLite 文件验证本地阅读记录。"""

from pathlib import Path

from weread_tui.models import Book, Chapter, ChapterDocument
from weread_tui.positions import ContentAnchor
from weread_tui.progress import ProgressStore


def test_position_survives_reopening_database_and_accounts_are_isolated(tmp_path: Path) -> None:
    """tmp_path 保存真实数据库；重建存储对象后仍按账号读取相同字符。"""
    path = tmp_path / "progress.sqlite3"
    document = ChapterDocument(Book("alice", "book", "书"), Chapter("chapter", "章"), "原文", "txt")
    anchor = ContentAnchor("t0", 1, document.revision)
    ProgressStore(path).save(document, anchor, updated_at=123.5)
    reopened = ProgressStore(path)
    saved = reopened.load("alice", "book")
    assert saved is not None
    assert saved.chapter_uid == "chapter"
    assert saved.anchor == anchor
    assert saved.updated_at == 123.5
    assert reopened.load("bob", "book") is None


def test_latest_chapter_and_previous_content_versions_remain_available(tmp_path: Path) -> None:
    """tmp_path 隔离数据库；切章更新最近位置，新版本不覆盖旧版本记录。"""
    store = ProgressStore(tmp_path / "progress.sqlite3")
    first = ChapterDocument(Book("alice", "book", "书"), Chapter("first", "首章"), "旧正文", "txt")
    old = store.save(first, ContentAnchor("t0", 1, first.revision), updated_at=10)
    second = ChapterDocument(first.book, Chapter("second", "次章"), "下一章", "txt")
    store.save(second, ContentAnchor("t0", 2, second.revision), updated_at=20)
    latest = store.load("alice", "book")
    assert latest is not None and latest.chapter_uid == "second"
    assert store.load("alice", "book", "first") == old
    changed = ChapterDocument(first.book, first.chapter, "新正文", "txt")
    store.save(changed, ContentAnchor("t0", 2, changed.revision), updated_at=30)
    assert store.load("alice", "book", "first", first.revision) == old
    latest = store.load("alice", "book")
    assert latest is not None and latest.anchor.revision == changed.revision
