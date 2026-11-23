"""选择续读来源并加载对应章节，云端失败时保留本地记录。"""

from dataclasses import dataclass, replace
from time import time
from typing import Literal

from weread_tui.chapter_protocol import ContentError
from weread_tui.cloud_progress import CloudProgress, choose_progress, cloud_anchor
from weread_tui.models import Book, Chapter, ChapterDocument
from weread_tui.positions import ContentAnchor, PositionError
from weread_tui.progress import ProgressStore, ReadingProgress
from weread_tui.provider import ProviderError, WereadProvider


@dataclass(frozen=True)
class ResumePlan:
    """position 是选中的记录，fallback 保留云端恢复失败时可用的本地记录。"""

    position: ReadingProgress | CloudProgress
    fallback: ReadingProgress | None
    notice: str

    def local_fallback(self, reason: str) -> "ResumePlan | None":
        """仅在当前来源是云端时按 reason 回退一次，避免循环或覆盖旧记录。"""
        if isinstance(self.position, CloudProgress) and self.fallback is not None:
            return ResumePlan(self.fallback, None, f"{reason} 采用本地阅读位置。")
        return None


@dataclass(frozen=True)
class ResumePoint:
    """anchor 已转换为本地字符，updated_at 保留原阅读时间，source/notice 说明来源。"""

    anchor: ContentAnchor
    updated_at: float | None
    source: Literal["local", "cloud"]
    notice: str


async def select_resume(provider: WereadProvider, store: ProgressStore, book: Book) -> ResumePlan | None:
    """通过 provider 只读查询 book 的位置，与 store 的本地实际阅读时间比较。"""
    local = store.load(book.account_id, book.book_id)
    try:
        cloud = await provider.get_cloud_progress(book)
    except ProviderError as error:
        if local is None:
            raise
        return ResumePlan(local, None, f"云端位置读取失败：{error} 采用本地阅读位置。")
    now = time()
    if cloud is not None and cloud.updated_at is not None and cloud.updated_at > now:
        cloud = replace(cloud, updated_at=None)
    chosen = choose_progress(local, cloud, now=now)
    if chosen == "cloud":
        assert cloud is not None
        notice = "采用云端阅读位置。"
        if cloud.updated_at is None:
            notice += "云端阅读时间未知，无法比较新旧。"
        return ResumePlan(cloud, local, notice)
    if chosen == "local":
        assert local is not None
        return ResumePlan(local, None, "采用本地阅读位置。")
    return None


async def load_resume(provider: WereadProvider, book: Book, chapters: list[Chapter],
                      plan: ResumePlan) -> tuple[ChapterDocument, ResumePoint, ResumePlan]:
    """加载 plan 的章节并转换锚点，读取或映射云端失败时只回退一次本地记录。"""
    try:
        chapter = next((chapter for chapter in chapters if chapter.uid == plan.position.chapter_uid), None)
        if chapter is None:
            raise PositionError("保存的章节已不在目录中，旧记录已保留。")
        document = await provider.get_chapter(book, chapter)
        position = plan.position
        if isinstance(position, CloudProgress):
            if document.from_cache:
                raise PositionError("仅有缓存正文，不能用它确认新的云端坐标。")
            point = ResumePoint(cloud_anchor(document, position), position.updated_at, "cloud", plan.notice)
        else:
            point = ResumePoint(position.anchor, position.updated_at, "local", plan.notice)
        return document, point, plan
    except (ContentError, ProviderError, PositionError) as error:
        fallback = plan.local_fallback(f"云端位置无法恢复：{error}")
        if fallback is None:
            raise
        return await load_resume(provider, book, chapters, fallback)
