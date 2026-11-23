"""微信读书统一数据入口；首版只调用官方只读能力。"""

from dataclasses import asdict, replace
from hashlib import sha256
from math import isfinite
from pathlib import Path
from typing import Any

import httpx

from weread_tui.account import GATEWAY, SKILL_VERSION, create_web_client
from weread_tui.chapter_protocol import ContentError
from weread_tui.credentials import Credentials
from weread_tui.web_reader import ChapterUnavailable, fetch_chapter, fetch_stylesheet
from weread_tui.models import Book as Book, Chapter as Chapter, ChapterDocument as ChapterDocument
from weread_tui.resources import ImageResources, load_images
from weread_tui.cloud_progress import CloudProgress
from weread_tui.read_cache import ReadCache


class ProviderError(Exception):
    """可在阅读界面显示的数据源错误。"""


class AuthenticationExpired(ProviderError):
    """官方网关明确拒绝了当前凭据。"""


class ProviderUnavailable(ProviderError):
    """只读网关网络不可用，可采用已有的本地阅读缓存。"""


class WereadProvider:
    """对外统一书架与目录接口，网页正文能力在后续票据引入。"""

    capabilities = frozenset({"bookshelf", "chapters", "chapter_content", "cloud_progress"})

    def __init__(self, credentials: Credentials, transport: httpx.AsyncBaseTransport | None = None,
                 cache_root: Path | None = None, *, cache_reads: bool = False) -> None:
        """credentials 固定账号，transport/cache_root 指定边界，cache_reads 启用断网阅读缓存。"""
        self.credentials = replace(credentials)
        self.account_id = credentials.vid or f"key:{sha256(credentials.api_key.encode()).hexdigest()}"
        self.transport = transport
        self.cache_root = cache_root if cache_root is not None else Path.home() / ".cache/weread-tui/resources"
        self.upgrade_available = False
        self.read_cache = ReadCache(self.cache_root.parent / "reading", self.account_id) if cache_reads else None
        self.using_cache = False

    async def _request(self, api_name: str, **parameters: str) -> dict[str, Any]:
        """调用 api_name，只读业务 parameters 与版本字段保持平铺。"""
        if not self.credentials.api_key:
            raise AuthenticationExpired("请先补充官方 API Key。")
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=20) as client:
                response = await client.post(
                    GATEWAY, headers={"Authorization": f"Bearer {self.credentials.api_key}"},
                    json={"api_name": api_name, "skill_version": SKILL_VERSION, **parameters},
                )
                if response.status_code in (401, 403):
                    raise AuthenticationExpired("官方 API Key 已失效，请重新登录或补充密钥。")
                if response.status_code == 429:
                    raise ProviderError("请求过于频繁，请稍后刷新。")
                response.raise_for_status()
                data: dict[str, Any] = response.json()
                if not isinstance(data, dict):
                    raise ProviderError("微信读书返回格式异常，请稍后刷新。")
        except httpx.RequestError:
            raise ProviderUnavailable("暂时无法连接微信读书，请检查网络后刷新。") from None
        except httpx.HTTPError:
            raise ProviderError("暂时无法连接微信读书，请检查网络后刷新。") from None
        except ValueError:
            raise ProviderError("微信读书返回格式异常，请稍后刷新。") from None
        code = data.get("errcode", 0)
        if code in (-2010, -2012):
            raise AuthenticationExpired("官方 API Key 已失效，请重新登录或补充密钥。")
        if code:
            raise ProviderError(f"微信读书接口返回错误（{code}），请稍后刷新。")
        if data.get("upgrade_info"):
            self.upgrade_available = True
        return data

    async def get_bookshelf(self) -> list[Book]:
        """返回全部可见书架条目，非电子书明确标记类型。"""
        try:
            data = await self._request("/shelf/sync")
        except ProviderUnavailable:
            cached = self.read_cache.load(("shelf",)) if self.read_cache else None
            if cached is None:
                raise
            self.using_cache = True
            return [Book(**book) for book in cached["books"]]
        account = self.account_id
        books = [Book(account, str(book["bookId"]), str(book["title"]), str(book.get("author", "")))
                 for book in data.get("books", [])]
        for entry in data.get("albums", []):
            album = entry["albumInfo"]
            books.append(Book(account, str(album["albumId"]), str(album["name"]),
                              str(album.get("authorName", "")), kind="audio"))
        if data.get("mp"):
            books.append(Book(account, "mp", "文章收藏", kind="articles"))
        if self.read_cache is not None:
            self.read_cache.save(("shelf",), {"books": [asdict(book) for book in books]})
        return books

    async def get_chapters(self, book: Book) -> list[Chapter]:
        """读取 book 的层级目录，不混用账号，也不为有声书请求电子书目录。"""
        if book.account_id != self.account_id:
            raise ProviderError("账号已变化，请重新打开书架。")
        if book.kind != "book":
            raise ProviderError("首版暂不支持有声书或文章收藏阅读。")
        try:
            data = await self._request("/book/chapterinfo", bookId=book.book_id)
        except ProviderUnavailable:
            cached = self.read_cache.load(("chapters", book.book_id)) if self.read_cache else None
            if cached is None:
                raise
            self.using_cache = True
            return [Chapter(**chapter) for chapter in cached["chapters"]]
        chapters = [Chapter(str(chapter["chapterUid"]), str(chapter["title"]), int(chapter.get("level", 1)))
                    for chapter in data.get("chapters", [])]
        if self.read_cache is not None:
            self.read_cache.save(("chapters", book.book_id), {"chapters": [asdict(chapter) for chapter in chapters]})
        return chapters

    async def get_chapter(self, book: Book, chapter: Chapter) -> ChapterDocument:
        """读取当前账号 book 中的 chapter，并保留未经显示转换的原文。"""
        if book.account_id != self.account_id:
            raise ProviderError("账号已变化，请重新打开书架。")
        if not self.credentials.skey:
            raise ContentError("缺少网页会话，请先扫码登录。")
        key = ("chapter", book.book_id, chapter.uid)
        try:
            async with create_web_client(self.credentials, self.transport) as client:
                source, format_name = await fetch_chapter(client, book.book_id, chapter.uid)
        except ChapterUnavailable:
            cached = self.read_cache.load(key) if self.read_cache else None
            if cached is None:
                raise
            self.using_cache = True
            return ChapterDocument(book, chapter, cached["source"], cached["format"], from_cache=True)
        if self.read_cache is not None:
            self.read_cache.save(key, {"source": source, "format": format_name})
        return ChapterDocument(book, chapter, source, format_name)

    async def get_cloud_progress(self, book: Book) -> CloudProgress | None:
        """只读获取 book 的云端位置，更新时间仅取 book.updateTime。"""
        if book.account_id != self.account_id:
            raise ProviderError("账号已变化，请重新打开书架。")
        data = await self._request("/book/getprogress", bookId=book.book_id)
        if str(data.get("bookId", "")) != book.book_id:
            raise ProviderError("云端进度的书籍标识不匹配，未恢复该位置。")
        position = data.get("book")
        if not isinstance(position, dict) or not position.get("chapterUid"):
            return None
        offset = position.get("chapterOffset")
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ProviderError("云端章内偏移无效，未恢复该位置。")
        stamp = position.get("updateTime")
        updated_at = float(stamp) if isinstance(stamp, (int, float)) and not isinstance(stamp, bool) else None
        if updated_at is not None and (not isfinite(updated_at) or updated_at <= 0):
            updated_at = None
        return CloudProgress(self.account_id, book.book_id, str(position["chapterUid"]), offset, updated_at)

    async def get_images(self, document: ChapterDocument) -> ImageResources:
        """获取 document 的图片，按当前账号及内容版本复用缓存。"""
        if document.book.account_id != self.account_id:
            raise ProviderError("账号已变化，请重新打开书架。")
        async with create_web_client(self.credentials, self.transport) as client:
            return await load_images(client, document, self.cache_root, self.transport)

    async def get_stylesheet(self, document: ChapterDocument) -> str:
        """获取 document 的原书排版样式，TXT 不请求 EPUB 样式。"""
        if document.book.account_id != self.account_id:
            raise ProviderError("账号已变化，请重新打开书架。")
        if document.format == "txt":
            return ""
        key = ("stylesheet", document.book.book_id, document.chapter.uid, document.revision)
        try:
            async with create_web_client(self.credentials, self.transport) as client:
                stylesheet = await fetch_stylesheet(client, document.book.book_id, document.chapter.uid)
        except ChapterUnavailable:
            cached = self.read_cache.load(key) if self.read_cache else None
            if cached is None:
                raise
            self.using_cache = True
            return str(cached["stylesheet"])
        if self.read_cache is not None:
            self.read_cache.save(key, {"stylesheet": stylesheet})
        return stylesheet
