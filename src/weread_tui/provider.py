"""微信读书统一数据入口；首版只调用官方只读能力。"""

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Any

import httpx

from weread_tui.account import GATEWAY, SKILL_VERSION, create_web_client
from weread_tui.chapter_protocol import ContentError
from weread_tui.credentials import Credentials
from weread_tui.web_reader import fetch_chapter, fetch_stylesheet
from weread_tui.models import Book as Book, Chapter as Chapter, ChapterDocument as ChapterDocument
from weread_tui.resources import ImageResources, load_images


class ProviderError(Exception):
    """可在阅读界面显示的数据源错误。"""


class AuthenticationExpired(ProviderError):
    """官方网关明确拒绝了当前凭据。"""


class WereadProvider:
    """对外统一书架与目录接口，网页正文能力在后续票据引入。"""

    capabilities = frozenset({"bookshelf", "chapters", "chapter_content"})

    def __init__(self, credentials: Credentials, transport: httpx.AsyncBaseTransport | None = None,
                 cache_root: Path | None = None) -> None:
        """credentials 固定账号，transport 与 cache_root 分别指定 HTTP 和缓存边界。"""
        self.credentials = replace(credentials)
        self.account_id = credentials.vid or f"key:{sha256(credentials.api_key.encode()).hexdigest()}"
        self.transport = transport
        self.cache_root = cache_root if cache_root is not None else Path.home() / ".cache/weread-tui/resources"
        self.upgrade_available = False

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
        data = await self._request("/shelf/sync")
        account = self.account_id
        books = [Book(account, str(book["bookId"]), str(book["title"]), str(book.get("author", "")))
                 for book in data.get("books", [])]
        for entry in data.get("albums", []):
            album = entry["albumInfo"]
            books.append(Book(account, str(album["albumId"]), str(album["name"]),
                              str(album.get("authorName", "")), kind="audio"))
        if data.get("mp"):
            books.append(Book(account, "mp", "文章收藏", kind="articles"))
        return books

    async def get_chapters(self, book: Book) -> list[Chapter]:
        """读取 book 的层级目录，不混用账号，也不为有声书请求电子书目录。"""
        if book.account_id != self.account_id:
            raise ProviderError("账号已变化，请重新打开书架。")
        if book.kind != "book":
            raise ProviderError("首版暂不支持有声书或文章收藏阅读。")
        data = await self._request("/book/chapterinfo", bookId=book.book_id)
        return [Chapter(str(chapter["chapterUid"]), str(chapter["title"]), int(chapter.get("level", 1)))
                for chapter in data.get("chapters", [])]

    async def get_chapter(self, book: Book, chapter: Chapter) -> ChapterDocument:
        """读取当前账号 book 中的 chapter，并保留未经显示转换的原文。"""
        if book.account_id != self.account_id:
            raise ProviderError("账号已变化，请重新打开书架。")
        if not self.credentials.skey:
            raise ContentError("缺少网页会话，请先扫码登录。")
        async with create_web_client(self.credentials, self.transport) as client:
            source, format_name = await fetch_chapter(client, book.book_id, chapter.uid)
        return ChapterDocument(book, chapter, source, format_name)

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
        async with create_web_client(self.credentials, self.transport) as client:
            return await fetch_stylesheet(client, document.book.book_id, document.chapter.uid)
