"""按账号隔离、按正文版本保留的 SQLite 阅读记录。"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import sqlite3
import time

from weread_tui.models import ChapterDocument
from weread_tui.positions import ContentAnchor


@dataclass(frozen=True)
class ReadingProgress:
    """account_id/book_id/chapter_uid 标识内容，anchor 定位字符，updated_at 是实际阅读时间。"""

    account_id: str
    book_id: str
    chapter_uid: str
    anchor: ContentAnchor
    updated_at: float


class ProgressStore:
    """每次位置变化立即提交，读取和重排不修改阅读时间。"""

    def __init__(self, path: Path) -> None:
        """path 指向本地数据库，各操作结束即提交并关闭连接。"""
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as database:
            database.execute("""
                CREATE TABLE IF NOT EXISTS reading_progress (
                    account_id TEXT NOT NULL, book_id TEXT NOT NULL, chapter_uid TEXT NOT NULL,
                    revision TEXT NOT NULL, run_id TEXT NOT NULL, text_offset INTEGER NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (account_id, book_id, chapter_uid, revision)
                )
            """)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """提供短生命周期连接；事务成功时提交，异常时回滚。"""
        database = sqlite3.connect(self.path)
        try:
            with database:
                yield database
        finally:
            database.close()

    def save(self, document: ChapterDocument, anchor: ContentAnchor, *, updated_at: float | None = None) -> ReadingProgress:
        """保存 document 的 anchor；updated_at 默认取本次实际位置变化的 Unix 时间。"""
        position = ReadingProgress(document.book.account_id, document.book.book_id, document.chapter.uid,
                                   anchor, time.time() if updated_at is None else updated_at)
        with self._connect() as database:
            database.execute("""
                INSERT INTO reading_progress VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (account_id, book_id, chapter_uid, revision) DO UPDATE SET
                    run_id=excluded.run_id, text_offset=excluded.text_offset, updated_at=excluded.updated_at
            """, (position.account_id, position.book_id, position.chapter_uid, anchor.revision,
                  anchor.run_id, anchor.text_offset, position.updated_at))
        return position

    def load(self, account_id: str, book_id: str, chapter_uid: str | None = None,
             revision: str | None = None) -> ReadingProgress | None:
        """读取 account_id/book_id 的最近位置，chapter_uid/revision 可限定章节或历史版本。"""
        query = "SELECT account_id, book_id, chapter_uid, revision, run_id, text_offset, updated_at FROM reading_progress"
        query += " WHERE account_id=? AND book_id=?"
        parameters = [account_id, book_id]
        for column, value in (("chapter_uid", chapter_uid), ("revision", revision)):
            if value is not None:
                query += f" AND {column}=?"
                parameters.append(value)
        with self._connect() as database:
            row = database.execute(query + " ORDER BY updated_at DESC, rowid DESC LIMIT 1", parameters).fetchone()
        if row is None:
            return None
        return ReadingProgress(str(row[0]), str(row[1]), str(row[2]),
                               ContentAnchor(str(row[4]), int(row[5]), str(row[3])), float(row[6]))
