"""用真实界面、SQLite 和原生书页验证重启恢复。"""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
import sys
from typing import Any

import httpx
import pytest
from textual.pilot import Pilot
from textual_image.renderable.tgp import Image as TGPRenderable

from weread_tui.account import AccountService
from weread_tui.app import WereadApp
from weread_tui.credentials import Credentials, CredentialStore
from weread_tui.graphics_screen import GraphicsScreen
from weread_tui.models import Book, Chapter, ChapterDocument
from weread_tui.progress import ProgressStore
from weread_tui.provider import WereadProvider
from weread_tui.terminal import TerminalCapabilities
from weread_tui import terminal
from weread_tui.chapter_protocol import encode_id
from weread_tui.reader import ReaderScreen
from weread_tui.source_positions import prepare_document


def sample_document(account_id: str = "reader", source: str | None = None) -> ChapterDocument:
    """account_id 区分用户，source 可替换以验证正文版本变化。"""
    return ChapterDocument(Book(account_id, "1", "书"), Chapter("2", "章"),
                           source if source is not None else "重复句中文 English 😀。" * 400, "txt")


@asynccontextmanager
async def graphic_view(path: Path, document: ChapterDocument, store: ProgressStore,
                       size: tuple[int, int] = (80, 30)) -> AsyncIterator[tuple[WereadApp, Pilot[None], GraphicsScreen]]:
    """path 隔离缓存；document/store 为真实阅读输入，size 指定重启后的窗口。"""
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    account = AccountService(CredentialStore(), transport=transport)
    provider = WereadProvider(Credentials(vid=document.book.account_id, skey="session"), transport, path / "resources")
    app = WereadApp(account=account)
    async with app.run_test(size=size) as pilot:
        screen = GraphicsScreen(provider, document, TerminalCapabilities(8, 16, True), progress=store)
        await app.push_screen(screen)
        await pilot.pause()
        await asyncio.wait_for(app.workers.wait_for_complete(), 15)
        yield app, pilot, screen


async def test_restart_restores_character_with_new_layout_without_changing_read_time(tmp_path: Path) -> None:
    """tmp_path 保存数据库；退出前提交的字符在不同窗口重启后仍在显示页内。"""
    path = tmp_path / "progress.sqlite3"
    store = ProgressStore(path)
    document = sample_document()
    async with graphic_view(tmp_path, document, store) as (app, pilot, screen):
        await pilot.press("l", "l")
        await asyncio.wait_for(app.workers.wait_for_complete(), 15)
        saved = store.load("reader", "1")
        assert saved is not None
        assert saved.anchor == screen.reading_anchor
        await pilot.press("plus", "shift+up")
        await asyncio.wait_for(app.workers.wait_for_complete(), 15)
        assert store.load("reader", "1") == saved
        await pilot.press("escape")
    reopened = ProgressStore(path)
    async with graphic_view(tmp_path, document, reopened, (100, 40)) as (_, _, screen):
        assert screen.reading_anchor == saved.anchor
        assert screen.session.page_map is not None
        assert screen.session.page_map.locate(saved.anchor) == screen.current_page
        assert reopened.load("reader", "1") == saved


async def test_changed_content_keeps_old_record_until_user_reads_new_version(tmp_path: Path) -> None:
    """tmp_path 隔离真实数据库；版本失效时明确提示，不以章首覆盖旧位置。"""
    store = ProgressStore(tmp_path / "progress.sqlite3")
    old_document = sample_document()
    async with graphic_view(tmp_path, old_document, store) as (app, pilot, _):
        await pilot.press("l")
        await asyncio.wait_for(app.workers.wait_for_complete(), 15)
    old = store.load("reader", "1")
    assert old is not None
    changed = sample_document(source="新的正文变化。" * 500)
    async with graphic_view(tmp_path, changed, store) as (app, pilot, screen):
        assert "无法准确恢复" in screen.position_notice
        assert "旧记录已保留" in screen.position_notice
        assert screen.current_page == 0
        assert store.load("reader", "1") == old
        await pilot.press("plus")
        await asyncio.wait_for(app.workers.wait_for_complete(), 15)
        assert store.load("reader", "1") == old
        await pilot.press("l")
        await asyncio.wait_for(app.workers.wait_for_complete(), 15)
        latest = store.load("reader", "1")
        assert latest is not None and latest.anchor.revision == changed.revision
        assert store.load("reader", "1", "2", old_document.revision) == old


async def test_graphic_progress_is_isolated_between_accounts(tmp_path: Path) -> None:
    """tmp_path 共用数据库；同一本书的不同账号互不恢复或覆盖位置。"""
    store = ProgressStore(tmp_path / "progress.sqlite3")
    async with graphic_view(tmp_path, sample_document("alice"), store) as (app, pilot, _):
        await pilot.press("l", "l")
        await asyncio.wait_for(app.workers.wait_for_complete(), 15)
    alice = store.load("alice", "1")
    assert alice is not None and alice.anchor.text_offset > 0
    async with graphic_view(tmp_path, sample_document("bob"), store) as (_, _, screen):
        assert screen.current_page == 0
        assert store.load("alice", "1") == alice
        bob = store.load("bob", "1")
        assert bob is not None and bob.anchor.text_offset == 0


async def test_progress_is_committed_before_neighbour_render_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """tmp_path 隔离数据库；monkeypatch 延迟原生邻页，已显示位置仍须立即提交。"""
    path = tmp_path / "progress.sqlite3"
    store = ProgressStore(path)
    async with graphic_view(tmp_path, sample_document(), store) as (app, pilot, screen):
        spawn = asyncio.create_subprocess_exec
        started = asyncio.Event()
        children: list[asyncio.subprocess.Process] = []

        async def slow_neighbour(*command: str, **options: Any) -> asyncio.subprocess.Process:
            """command/options 来自邻页任务，只替换系统进程以暂停预加载。"""
            child = await spawn(sys.executable, "-c", "import sys,time; sys.stdin.read(); time.sleep(60)", **options)
            children.append(child)
            started.set()
            return child

        monkeypatch.setattr(asyncio, "create_subprocess_exec", slow_neighbour)
        app.simulate_key("l")
        await asyncio.wait_for(started.wait(), 1)
        assert screen.current_page == 1
        saved = ProgressStore(path).load("reader", "1")
        assert saved is not None and saved.anchor == screen.reading_anchor
        assert saved.anchor.text_offset > 0
        await pilot.press("escape")
        assert all(child.returncode is not None for child in children)
    assert ProgressStore(path).load("reader", "1") == saved


async def test_unmapped_character_reports_failure_and_preserves_saved_record(tmp_path: Path) -> None:
    """tmp_path 隔离数据库；同版正文的隐藏字符不应被当成准确恢复。"""
    source = '<p style="display:none">隐藏位置</p><p>可见的正文。</p>'
    document = ChapterDocument(Book("reader", "1", "书"), Chapter("2", "章"), source, "epub")
    anchor = prepare_document(source).anchor_from_source(source.index("隐藏"))
    store = ProgressStore(tmp_path / "progress.sqlite3")
    saved = store.save(document, anchor, updated_at=123)
    async with graphic_view(tmp_path, document, store) as (_, _, screen):
        assert "无法准确恢复" in screen.position_notice
        assert "旧记录已保留" in screen.position_notice
        assert store.load("reader", "1") == saved


async def test_selecting_book_resumes_saved_chapter_and_chapter_change_is_committed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """tmp_path 隔离数据库和缓存；monkeypatch 仅替换终端能力及用户数据根目录。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(terminal, "Image", TGPRenderable)
    fixture = json.loads((Path(__file__).parent / "fixtures/chapter.json").read_text())
    chapters = [Chapter(str(uid), f"第 {uid} 章") for uid in (2, 3, 4)]
    encoded = {encode_id(chapter.uid): chapter.uid for chapter in chapters}

    def respond(request: httpx.Request) -> httpx.Response:
        """request 经过正式书架、正文校验和样式读取路径，数据均来自自造样本。"""
        if request.url.path.startswith("/web/reader/"):
            uid = encoded[request.url.path.rsplit("k", 1)[1]]
            state = {"reader": {"psvts": "state", "bookInfo": {"bookId": "1"},
                                "currentChapter": {"chapterUid": uid}}}
            return httpx.Response(200, text=f"<script>window.__INITIAL_STATE__={json.dumps(state)};</script>")
        if request.url.path == "/api/userInfo":
            return httpx.Response(200, json={"name": "读者"})
        if request.url.path.startswith("/web/book/chapter/"):
            endpoint = request.url.path.rsplit("/", 1)[1]
            if endpoint == "e_2":
                return httpx.Response(200, json={})
            return httpx.Response(200, text=fixture["parts"][("e_0", "e_1", "e_3").index(endpoint)])
        data = json.loads(request.content)
        if data["api_name"] == "/shelf/sync":
            return httpx.Response(200, json={"books": [{"bookId": "1", "title": "书"}]})
        if data["api_name"] == "/book/chapterinfo":
            return httpx.Response(200, json={"chapters": [
                {"chapterUid": chapter.uid, "title": chapter.title} for chapter in chapters]})
        assert data["api_name"] == "/_list"
        return httpx.Response(200, json={})

    path = tmp_path / "progress.sqlite3"
    store = ProgressStore(path)
    document = ChapterDocument(Book("reader", "1", "书"), chapters[1], fixture["source"], "epub")
    anchor = prepare_document(document.source).anchor_from_source(document.source.index("微信"))
    saved = store.save(document, anchor, updated_at=123)
    credentials = CredentialStore()
    credentials.save(Credentials(vid="reader", skey="session", api_key="wrk-test"))
    account = AccountService(credentials, transport=httpx.MockTransport(respond))
    app = WereadApp(account=account, progress=store)
    async with app.run_test(size=(100, 40)) as pilot:
        await asyncio.wait_for(app.workers.wait_for_complete(), 5)
        await pilot.click("#bookshelf")
        await pilot.pause()
        await pilot.click("#books ListItem")

        async def wait_for_restored_page() -> GraphicsScreen:
            """从公开可见的书页判断自动恢复已经完成。"""
            while True:
                screen = app.screen
                if isinstance(screen, GraphicsScreen) and screen.picture.image is not None and not screen.rendering:
                    return screen
                await pilot.pause(0.02)

        screen = await asyncio.wait_for(wait_for_restored_page(), 15)
        assert screen.document.chapter.uid == "3"
        assert screen.reading_anchor == anchor
        assert store.load("reader", "1") == saved
        await pilot.press("escape")
        assert isinstance(app.screen, ReaderScreen)
        await pilot.press("]")
        await asyncio.wait_for(app.workers.wait_for_complete(), 5)
        changed = store.load("reader", "1")
        assert changed is not None and changed.chapter_uid == "4"
        await pilot.press("r")
        await asyncio.wait_for(app.workers.wait_for_complete(), 5)
        assert store.load("reader", "1") == changed
    assert ProgressStore(path).load("reader", "1") == changed
