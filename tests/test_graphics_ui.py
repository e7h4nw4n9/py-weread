"""在无头界面中验证真实图形页翻页与关闭清理。"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx
from textual import events
from textual._xterm_parser import XTermParser

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


@pytest.mark.parametrize("delayed_handle", [False, True])
async def test_graphics_view_closes_during_native_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, delayed_handle: bool,
) -> None:
    """tmp_path 隔离会话；monkeypatch 模拟长进程，delayed_handle 延迟系统句柄交付。"""
    spawn = asyncio.create_subprocess_exec
    started = asyncio.Event()
    release = asyncio.Event()
    children: list[asyncio.subprocess.Process] = []

    async def slow_process(*command: str, **options: Any) -> asyncio.subprocess.Process:
        """command 来自被测服务；options 保留真实进程管道和回收机制。"""
        process = await spawn(sys.executable, "-c", "import sys,time; sys.stdin.read(); time.sleep(60)", **options)
        children.append(process)
        started.set()
        if delayed_handle:
            await release.wait()
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
        try:
            app.simulate_key("escape")
            if delayed_handle:
                async def wait_for_close() -> None:
                    """等界面进入关闭流程后再交付进程句柄。"""
                    while not screen.closing:
                        await asyncio.sleep(0.01)

                await asyncio.wait_for(wait_for_close(), 3)
        finally:
            release.set()
        await pilot.pause()
        assert children[0].returncode is not None
        assert not screen.session.directory.exists()


async def test_font_and_spacing_keep_current_character(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """tmp_path 隔离资源；monkeypatch 在成功重排后模拟外部渲染失败。"""
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    account = AccountService(CredentialStore(), transport=transport)
    provider = WereadProvider(Credentials(vid="reader", skey="session"), transport, tmp_path)
    document = ChapterDocument(Book("reader", "1", "书"), Chapter("2", "长段"),
                               "重复句 English 中文 😀 e\u0301。" * 300, "txt")
    app = WereadApp(account=account)
    async with app.run_test(size=(80, 30)) as pilot:
        screen = GraphicsScreen(provider, document, TerminalCapabilities(8, 16, True))
        await app.push_screen(screen)
        await pilot.pause()
        await asyncio.wait_for(app.workers.wait_for_complete(), timeout=15)
        await pilot.press("l", "l")
        await asyncio.wait_for(app.workers.wait_for_complete(), timeout=15)
        assert screen.current_page == 2
        assert screen.session.page_map is not None
        anchor = screen.session.page_map.first_anchor(screen.current_page)
        assert anchor is not None
        # 单独等待缩小完成，避免后续放大取消了缩小任务而漏测。
        await pilot.press("minus")
        await asyncio.wait_for(app.workers.wait_for_complete(), timeout=15)
        assert screen.rendered_settings == (18, 1.6)
        assert screen.session.page_map is not None
        assert screen.session.page_map.locate(anchor) == screen.current_page
        await pilot.press("plus")
        await asyncio.wait_for(app.workers.wait_for_complete(), timeout=15)
        assert screen.rendered_settings == (20, 1.6)
        assert screen.session.page_map is not None
        assert screen.session.page_map.locate(anchor) == screen.current_page
        old_directory = screen.session.directory
        # 输入真实终端字符及 CSI 序列，避免测试只覆盖理想化的按键名。
        parser = XTermParser(False)
        keys = [event.key for sequence in ("+", "\x1b[43;2u", "-", "=")
                for event in parser.feed(sequence) if isinstance(event, events.Key)]
        await pilot.press(*keys)
        await asyncio.wait_for(app.workers.wait_for_complete(), timeout=15)
        assert screen.font_size == 24
        assert screen.rendered_settings == (24, 1.6)
        assert screen.session.page_map is not None
        assert screen.session.page_map.locate(anchor) == screen.current_page
        assert not old_directory.exists()
        await pilot.press("ctrl+up")
        assert screen.line_height == 1.6
        keys = [event.key for event in parser.feed("\x1b[1;2A") if isinstance(event, events.Key)]
        await pilot.press(*keys)
        await asyncio.wait_for(app.workers.wait_for_complete(), timeout=15)
        assert screen.line_height == 1.8
        assert screen.session.page_map is not None
        assert screen.session.page_map.locate(anchor) == screen.current_page
        keys = [event.key for event in parser.feed("\x1b[1;2B") if isinstance(event, events.Key)]
        await pilot.press(*keys)
        await asyncio.wait_for(app.workers.wait_for_complete(), timeout=15)
        assert screen.rendered_settings == (24, 1.6)
        assert screen.session.page_map is not None
        assert screen.session.page_map.locate(anchor) == screen.current_page
        await pilot.press("shift+equals_sign")
        await asyncio.wait_for(app.workers.wait_for_complete(), timeout=15)
        assert screen.rendered_settings == (26, 1.6)
        previous = screen.session
        previous_page = screen.current_page
        spawn = asyncio.create_subprocess_exec

        async def failed_render(*command: str, **options: Any) -> asyncio.subprocess.Process:
            """command 是外部渲染调用；options 保留进程管道与回收行为。"""
            return await spawn(sys.executable, "-c",
                               'import sys; sys.stdin.read(); print(\'{"error":"测试排版失败"}\')', **options)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", failed_render)
        await pilot.press("plus")
        await asyncio.wait_for(app.workers.wait_for_complete(), timeout=15)
        assert screen.session is previous
        assert screen.current_page == previous_page
        assert screen.font_size == 26
        assert screen.picture.image is not None
        assert screen.pending is None
        await pilot.press("escape")
