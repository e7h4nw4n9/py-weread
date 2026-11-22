"""通过界面与 HTTP 边界验证选书到目录的路径。"""

import json
import asyncio

import httpx
from textual.widgets import Label, ListItem, Static

from weread_tui.account import AccountService
from weread_tui.app import WereadApp
from weread_tui.credentials import Credentials, CredentialStore


async def test_selecting_book_shows_hierarchical_chapters() -> None:
    """用户从首页进入书架，选书后看到原始 UID 对应的层级目录。"""
    def respond(request: httpx.Request) -> httpx.Response:
        """根据 request 返回认证、书架与目录数据。"""
        if request.url.host == "weread.qq.com":
            return httpx.Response(200, json={"name": "读者"})
        data = json.loads(request.content)
        if data["api_name"] == "/shelf/sync":
            return httpx.Response(200, json={"books": [{"bookId": "b1", "title": "示例书"}]})
        if data["api_name"] == "/book/chapterinfo":
            assert data["bookId"] == "b1"
            return httpx.Response(200, json={"chapters": [
                {"chapterUid": "c8", "title": "第一部分", "level": 1},
                {"chapterUid": "c9", "title": "中文章节", "level": 2},
            ]})
        return httpx.Response(200, json={"errcode": 0})

    store = CredentialStore()
    store.save(Credentials(vid="reader", skey="session", api_key="wrk-test"))
    account = AccountService(store, transport=httpx.MockTransport(respond))
    app = WereadApp(account=account)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        await pilot.click("#bookshelf")
        await pilot.pause()
        await pilot.click("#books ListItem")
        await pilot.press("enter")
        await pilot.pause()
        labels = [str(label.content) for label in app.screen.query("#chapters Label").results(Label)]
        assert labels == ["第一部分", "  中文章节"]
        assert "示例书" in str(app.screen.query_one("#library-status", Static).content)
        await pilot.press("escape")
        assert app.query_one("#bookshelf").display


async def test_selecting_another_book_cancels_previous_catalogue() -> None:
    """前一本书目录仍在等待时切书，旧请求被取消且不覆盖新目录。"""
    first_pending = asyncio.Event()
    first_cancelled = asyncio.Event()

    async def respond(request: httpx.Request) -> httpx.Response:
        """按 request 模拟第一本慢、第二本快的目录响应。"""
        data = json.loads(request.content)
        if data["api_name"] == "/shelf/sync":
            return httpx.Response(200, json={"books": [
                {"bookId": "slow", "title": "第一本"}, {"bookId": "fast", "title": "第二本"},
            ]})
        if data.get("bookId") == "slow":
            first_pending.set()
            try:
                await asyncio.Future[None]()
            finally:
                first_cancelled.set()
        return httpx.Response(200, json={"chapters": [
            {"chapterUid": "fast-uid", "title": "第二本的章节"},
        ]})

    account = AccountService(CredentialStore(), transport=httpx.MockTransport(respond))
    account.credentials = Credentials(vid="reader", api_key="wrk-test")
    from weread_tui.library import LibraryScreen

    app = WereadApp(account=AccountService(CredentialStore()))
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.app.push_screen(LibraryScreen(account))
        await pilot.pause()
        rows = list(app.screen.query("#books ListItem").results(ListItem))
        await pilot.click(rows[0])
        await asyncio.wait_for(first_pending.wait(), timeout=2)
        await pilot.click(rows[1])
        await asyncio.wait_for(first_cancelled.wait(), timeout=2)
        await pilot.pause()
        labels = [str(label.content) for label in app.screen.query("#chapters Label").results(Label)]
        assert labels == ["第二本的章节"]
