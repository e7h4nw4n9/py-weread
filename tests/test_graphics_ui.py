"""在无头界面中验证真实图形页翻页与关闭清理。"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx

from weread_tui.account import AccountService
from weread_tui.app import WereadApp
from weread_tui.credentials import Credentials, CredentialStore
from weread_tui.graphics_screen import GraphicsScreen
from weread_tui.models import Book, Chapter, ChapterDocument
from weread_tui.provider import WereadProvider
from weread_tui.terminal import TerminalCapabilities
import pytest


async def test_graphics_pages_can_turn_and_close(tmp_path: Path) -> None:
    """tmp_path 隔离缓存；使用真实 WeasyPrint/PDFium 子进程，不伪造书页。"""
    def respond(request: httpx.Request) -> httpx.Response:
        """原书无额外 CSS，request 只需获得当前章节状态。"""
        if request.method == "GET":
            state = {"reader": {"psvts": "state", "bookInfo": {"bookId": "1"},
                                "currentChapter": {"chapterUid": 2}}}
            return httpx.Response(200, text=f"<script>window.__INITIAL_STATE__={json.dumps(state)};</script>")
        assert json.loads(request.content)["st"] == 1
        return httpx.Response(200, json={})

    account = AccountService(CredentialStore(), transport=httpx.MockTransport(respond))
    provider = WereadProvider(Credentials(vid="reader", skey="session"), account.transport, tmp_path)
    document = ChapterDocument(Book("reader", "1", "书"), Chapter("2", "章"),
        '<p>第一页 First</p><p style="break-before:page">第二页 Second</p>', "epub")
    app = WereadApp(account=account)
    async with app.run_test(size=(100, 40)) as pilot:
        screen = GraphicsScreen(provider, document, TerminalCapabilities(8, 16, True))
        await app.push_screen(screen)
        await pilot.pause()
        await asyncio.wait_for(app.workers.wait_for_complete(), timeout=15)
        assert screen.session.page_count == 2
        assert screen.picture.image is not None
        await pilot.press("l")
        await asyncio.wait_for(app.workers.wait_for_complete(), timeout=10)
        assert screen.current_page == 1
        directory = screen.session.directory
        await pilot.press("escape")
        assert screen.picture.image is None
        assert not directory.exists()


async def test_graphics_view_closes_during_native_render(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """tmp_path 隔离会话，monkeypatch 仅让外部原生进程稳定保持运行。"""
    spawn = asyncio.create_subprocess_exec
    started = asyncio.Event()
    children: list[asyncio.subprocess.Process] = []

    async def slow_process(*command: str, **options: Any) -> asyncio.subprocess.Process:
        """command 来自被测服务；options 保留真实进程管道和回收机制。"""
        process = await spawn(sys.executable, "-c", "import sys,time; sys.stdin.read(); time.sleep(60)", **options)
        children.append(process)
        started.set()
        return process

    def respond(request: httpx.Request) -> httpx.Response:
        """原书无额外 CSS，request 返回最小章节状态。"""
        if request.method == "GET":
            state = {"reader": {"psvts": "state", "bookInfo": {"bookId": "1"},
                                "currentChapter": {"chapterUid": 2}}}
            return httpx.Response(200, text=f"<script>window.__INITIAL_STATE__={json.dumps(state)};</script>")
        return httpx.Response(200, json={})

    monkeypatch.setattr(asyncio, "create_subprocess_exec", slow_process)
    account = AccountService(CredentialStore(), transport=httpx.MockTransport(respond))
    provider = WereadProvider(Credentials(vid="reader", skey="session"), account.transport, tmp_path)
    document = ChapterDocument(Book("reader", "1", "书"), Chapter("2", "章"), "<p>中文</p>", "epub")
    app = WereadApp(account=account)
    async with app.run_test(size=(100, 40)) as pilot:
        screen = GraphicsScreen(provider, document, TerminalCapabilities(8, 16, True))
        await app.push_screen(screen)
        await asyncio.wait_for(started.wait(), timeout=5)
        await pilot.press("escape")
        await pilot.pause()
        assert children[0].returncode is not None
        assert not screen.session.directory.exists()
