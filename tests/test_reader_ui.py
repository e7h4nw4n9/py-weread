"""从网页响应到可见中文正文的完整诊断阅读路径。"""

import asyncio
import json
import io
import zipfile
from pathlib import Path
from typing import Any

import httpx
from textual.widgets import Static
from textual_image.widget import Image as ImageWidget
from PIL import Image as PillowImage

from weread_tui.account import AccountService
from weread_tui.app import WereadApp
from weread_tui.credentials import Credentials, CredentialStore
from weread_tui.provider import Book, Chapter, WereadProvider
from weread_tui.reader import ReaderScreen
from weread_tui.chapter_protocol import encode_id


def sample() -> dict[str, Any]:
    """读取自造固定章节样本，预期正文不由被测解码器生成。"""
    data: dict[str, Any] = json.loads((Path(__file__).parent / "fixtures/chapter.json").read_text())
    return data


async def test_chinese_chapter_is_visible_and_original_is_preserved() -> None:
    """MD5 校验与整体解码后显示中文，样式不显示，原始 XHTML 不丢失。"""
    fixture = sample()

    def respond(request: httpx.Request) -> httpx.Response:
        """按 request 返回同一章节的状态与三个正文分片。"""
        if request.method == "GET":
            state = {"reader": {"psvts": "state", "bookInfo": {"bookId": "1"},
                                "currentChapter": {"chapterUid": 2}}}
            return httpx.Response(200, text=f"<script>window.__INITIAL_STATE__={json.dumps(state)};</script>")
        index = {"e_0": 0, "e_1": 1, "e_3": 2}[request.url.path.rsplit("/", 1)[1]]
        return httpx.Response(200, text=fixture["parts"][index])

    account = AccountService(CredentialStore(), transport=httpx.MockTransport(respond))
    credentials = Credentials(vid="reader", skey="session")
    provider = WereadProvider(credentials, account.transport)
    book, chapter = Book("reader", "1", "示例书"), Chapter("2", "示例章节")
    app = WereadApp(account=account)
    async with app.run_test(size=(100, 40)) as pilot:
        screen = ReaderScreen(provider, account, book, chapter)
        await app.push_screen(screen)
        await pilot.pause()
        visible = "\n".join(str(widget.content) for widget in screen.query("#reading-content Static").results(Static))
        assert "你好，微信读书。" in visible
        assert "color:red" not in visible
        assert screen.document is not None
        assert screen.document.source == fixture["source"]
        assert screen.document.revision == fixture["revision"]
        await pilot.press("escape")
        assert app.query_one("#welcome").display


async def test_returning_to_catalogue_cancels_chapter_request() -> None:
    """请求仍在等待时返回，不让后台 HTTP 请求继续占用资源。"""
    pending, cancelled = asyncio.Event(), asyncio.Event()

    async def respond(request: httpx.Request) -> httpx.Response:
        """让 request 一直等待，以验证取消传播到 HTTP 边界。"""
        pending.set()
        try:
            await asyncio.Future[None]()
        finally:
            cancelled.set()
        raise AssertionError("请求应被取消")

    account = AccountService(CredentialStore(), transport=httpx.MockTransport(respond))
    provider = WereadProvider(Credentials(vid="reader", skey="session"), account.transport)
    app = WereadApp(account=AccountService(CredentialStore()))
    async with app.run_test(size=(100, 40)) as pilot:
        await app.push_screen(ReaderScreen(provider, account, Book("reader", "1", "书"), Chapter("2", "章")))
        await asyncio.wait_for(pending.wait(), timeout=2)
        await pilot.press("escape")
        await asyncio.wait_for(cancelled.wait(), timeout=2)


async def test_illustrated_chapter_displays_image_in_original_order(tmp_path: Path) -> None:
    """tmp_path 隔离缓存；正文图片从资源包映射到实际图像组件。"""
    fixture = json.loads((Path(__file__).parent / "fixtures/illustrated-chapter.json").read_text())
    picture = io.BytesIO()
    PillowImage.new("RGB", (8, 8), "red").save(picture, format="PNG")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("Images/picture-key", picture.getvalue())

    def respond(request: httpx.Request) -> httpx.Response:
        """按 request 提供真实解码路径所需的自造图文响应。"""
        if request.url.host == "res.weread.qq.com":
            return httpx.Response(200, content=archive.getvalue())
        if request.method == "GET":
            state = {"reader": {"psvts": "state", "bookInfo": {"bookId": "1"},
                                "currentChapter": {"chapterUid": 2}}}
            return httpx.Response(200, text=f"<script>window.__INITIAL_STATE__={json.dumps(state)};</script>")
        if request.url.path == "/web/book/chapterInfos":
            return httpx.Response(200, json=[{"bookId": "1", "updated": [
                {"chapterUid": 2, "tar": "https://res.weread.qq.com/assets"},
            ]}])
        index = {"e_0": 0, "e_1": 1, "e_3": 2}[request.url.path.rsplit("/", 1)[1]]
        return httpx.Response(200, text=fixture["parts"][index])

    account = AccountService(CredentialStore(), transport=httpx.MockTransport(respond))
    provider = WereadProvider(Credentials(vid="reader", skey="session"), account.transport, tmp_path)
    app = WereadApp(account=account)
    async with app.run_test(size=(100, 40)) as pilot:
        screen = ReaderScreen(provider, account, Book("reader", "1", "书"), Chapter("2", "章"))
        await app.push_screen(screen)
        await asyncio.wait_for(app.workers.wait_for_complete(), timeout=5)
        await pilot.pause()
        content = screen.query_one("#reading-content")
        before, image_slot, after = content.children
        assert isinstance(before, Static) and "图片之前" in str(before.content)
        picture_widget = image_slot.query_one(ImageWidget)
        assert picture_widget.image is not None
        assert isinstance(after, Static) and "图片之后" in str(after.content)
        await pilot.press("escape")
        assert picture_widget.image is None


async def test_three_chapter_navigation_cancels_previous_images(tmp_path: Path) -> None:
    """tmp_path 隔离缓存；图片仍在下载时切章，旧图不得回写新章节。"""
    plain = sample()
    illustrated = json.loads((Path(__file__).parent / "fixtures/illustrated-chapter.json").read_text())
    chapters = [Chapter(str(uid), f"第 {uid} 章") for uid in (2, 3, 4)]
    encoded_ids = {encode_id(chapter.uid): chapter.uid for chapter in chapters}
    pending, cancelled = asyncio.Event(), asyncio.Event()

    async def respond(request: httpx.Request) -> httpx.Response:
        """按 request 模拟首章慢图片与后两章正常正文。"""
        if request.url.host == "res.weread.qq.com":
            pending.set()
            try:
                await asyncio.Future[None]()
            finally:
                cancelled.set()
            raise AssertionError("旧图片请求应被取消")
        if request.method == "GET":
            uid = encoded_ids[request.url.path.rsplit("k", 1)[1]]
            state = {"reader": {"psvts": "state", "bookInfo": {"bookId": "1"},
                                "currentChapter": {"chapterUid": uid}}}
            return httpx.Response(200, text=f"<script>window.__INITIAL_STATE__={json.dumps(state)};</script>")
        if request.url.path == "/web/book/chapterInfos":
            return httpx.Response(200, json=[{"bookId": "1", "updated": [
                {"chapterUid": 2, "tar": "https://res.weread.qq.com/slow-image"},
            ]}])
        uid = encoded_ids[json.loads(request.content)["c"]]
        fixture = illustrated if uid == "2" else plain
        index = {"e_0": 0, "e_1": 1, "e_3": 2}[request.url.path.rsplit("/", 1)[1]]
        return httpx.Response(200, text=fixture["parts"][index])

    account = AccountService(CredentialStore(), transport=httpx.MockTransport(respond))
    provider = WereadProvider(Credentials(vid="reader", skey="session"), account.transport, tmp_path)
    app = WereadApp(account=account)
    async with app.run_test(size=(100, 40)) as pilot:
        screen = ReaderScreen(provider, account, Book("reader", "1", "书"), chapters[0], chapters)
        await app.push_screen(screen)
        await asyncio.wait_for(pending.wait(), timeout=2)
        await pilot.press("]")
        await asyncio.wait_for(cancelled.wait(), timeout=2)
        await asyncio.wait_for(app.workers.wait_for_complete(), timeout=5)
        assert screen.document is not None and screen.document.chapter.uid == "3"
        assert not list(screen.query(ImageWidget))
        await pilot.press("]")
        await asyncio.wait_for(app.workers.wait_for_complete(), timeout=5)
        assert screen.document is not None and screen.document.chapter.uid == "4"
