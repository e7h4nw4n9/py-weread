"""真实 SQLite 与原生排版下的本地、云端续读选择。"""

import asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
import json
from pathlib import Path

import httpx
import pytest
from textual.pilot import Pilot
from textual_image.renderable.tgp import Image as TGPRenderable

from weread_tui import terminal
from weread_tui.account import AccountService
from weread_tui.app import WereadApp
from weread_tui.chapter_protocol import encode_id
from weread_tui.credentials import Credentials, CredentialStore
from weread_tui.graphics_screen import GraphicsScreen
from weread_tui.models import Book, Chapter, ChapterDocument
from weread_tui.progress import ProgressStore
from weread_tui.source_positions import prepare_document


class CloudScenario:
    """用固定自造正文提供官方只读 HTTP 响应，可切换网络和云端记录状态。"""

    def __init__(self, path: Path, mode: str, local_time: float | None, cloud_time: float | None) -> None:
        """path 隔离数据库，mode 控制服务状态，local_time/cloud_time 是实际阅读时间。"""
        self.mode, self.cloud_time = mode, cloud_time
        fixtures = Path(__file__).parent / "fixtures"
        self.fixture = json.loads((fixtures / "chapter.json").read_text())
        self.style = json.loads((fixtures / "hidden-heading-style.json").read_text())
        self.chapters = [Chapter(str(uid), f"第 {uid} 章") for uid in (2, 3)]
        self.encoded = {encode_id(chapter.uid): chapter.uid for chapter in self.chapters}
        self.book = Book("reader", "1", "书")
        self.store = ProgressStore(path / "progress.sqlite3")
        self.local_anchor = prepare_document(self.fixture["source"]).anchor_from_source(self.fixture["source"].index("你好"))
        if local_time is not None:
            document = ChapterDocument(self.book, self.chapters[0], self.fixture["source"], "epub")
            self.store.save(document, self.local_anchor, updated_at=local_time)
        self.credentials = CredentialStore()
        self.credentials.save(Credentials(vid="reader", skey="session", api_key="wrk-test"))

    def app(self) -> WereadApp:
        """为本次启动创建新账号服务，数据库和外部 HTTP 场景保持不变。"""
        return WereadApp(AccountService(self.credentials, httpx.MockTransport(self.respond)), progress=self.store)

    def respond(self, request: httpx.Request) -> httpx.Response:
        """request 仅可调用白名单中的读取业务，不允许进度写入或时长上报。"""
        if self.mode == "offline-all":
            raise httpx.ConnectError("offline", request=request)
        path = request.url.path
        if path == "/api/userInfo":
            return httpx.Response(200, json={})
        if path.startswith("/web/reader/"):
            uid = self.encoded[path.rsplit("k", 1)[1]]
            if uid == "3" and self.mode == "forbidden":
                return httpx.Response(403)
            state = {"reader": {"psvts": "state", "bookInfo": {"bookId": "1"}, "currentChapter": {"chapterUid": uid}}}
            return httpx.Response(200, text=f"<script>window.__INITIAL_STATE__={json.dumps(state)};</script>")
        if path.startswith("/web/book/chapter/"):
            endpoint = path.rsplit("/", 1)[1]
            if endpoint == "e_2":
                uid = self.encoded[json.loads(request.content)["c"]]
                return httpx.Response(200, text=self.style["part"]) if uid == "3" and self.mode == "invisible" else httpx.Response(200, json={})
            assert endpoint in ("e_0", "e_1", "e_3")
            return httpx.Response(200, text=self.fixture["parts"][("e_0", "e_1", "e_3").index(endpoint)])
        assert path == "/api/agent/gateway"
        data = json.loads(request.content)
        api = data["api_name"]
        assert api in ("/_list", "/shelf/sync", "/book/chapterinfo", "/book/getprogress")
        if api == "/shelf/sync":
            return httpx.Response(200, json={"books": [{"bookId": "1", "title": "书"}]})
        if api == "/book/chapterinfo":
            return httpx.Response(200, json={"chapters": [
                {"chapterUid": chapter.uid, "title": chapter.title} for chapter in self.chapters]})
        if api == "/book/getprogress":
            if self.mode == "cloud-offline":
                raise httpx.ConnectError("offline", request=request)
            offset = self.fixture["source"].index("示例" if self.mode == "invisible" else "微信")
            if self.mode == "bad-offset":
                offset = 10000
            cloud_uid = 9 if self.mode == "missing-chapter" else 3
            return httpx.Response(200, json={"bookId": "1", "timestamp": 9999999999,
                "book": {"chapterUid": cloud_uid, "chapterOffset": offset, "updateTime": self.cloud_time}})
        return httpx.Response(200, json={})


@asynccontextmanager
async def selected_book(scenario: CloudScenario) -> AsyncIterator[tuple[WereadApp, Pilot[None], GraphicsScreen]]:
    """使用 scenario 启动真实应用，从书架点击到自动出现最终书页。"""
    app = scenario.app()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        await asyncio.wait_for(app.workers.wait_for_complete(), 5)
        await pilot.click("#bookshelf")
        await pilot.pause()
        await pilot.click("#books ListItem")

        async def wait_for_page() -> GraphicsScreen:
            """以公开可见的书页判断恢复完成，而非仅等待某个内部函数。"""
            while True:
                screen = app.screen
                if isinstance(screen, GraphicsScreen) and screen.picture.image is not None and not screen.rendering:
                    return screen
                await pilot.pause(0.02)

        screen = await asyncio.wait_for(wait_for_page(), 15)
        yield app, pilot, screen


@pytest.mark.parametrize(("mode", "local_time", "cloud_time", "chapter_uid", "source", "saved_time"), [
    ("cloud-new", 100, 150, "3", "云端", 150),
    ("local-new", 200, 150, "2", "本地", 200),
    ("equal", 150, 150, "2", "本地", 150),
    ("missing-time", 100, None, "2", "本地", 100),
    ("cloud-offline", 100, 150, "2", "本地", 100),
    ("bad-offset", 100, 150, "2", "本地", 100),
    ("forbidden", 100, 150, "2", "本地", 100),
    ("invisible", 100, 150, "2", "本地", 100),
    ("missing-chapter", 100, 150, "2", "本地", 100),
    ("only-cloud", None, 150, "3", "云端", 150),
    ("unknown-only-cloud", None, None, "3", "云端", 0),
])
async def test_book_uses_newer_valid_position_without_fabricating_read_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, local_time: float | None,
    cloud_time: float | None, chapter_uid: str, source: str, saved_time: float,
) -> None:
    """tmp_path/monkeypatch 隔离系统边界；其余参数指定独立预期的来源、章节和时间。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(terminal, "Image", TGPRenderable)
    scenario = CloudScenario(tmp_path, mode, local_time, cloud_time)
    async with selected_book(scenario) as (_, _, screen):
        assert screen.document.chapter.uid == chapter_uid
        assert f"采用{source}阅读位置" in screen.position_notice
        saved = scenario.store.load("reader", "1")
        assert saved is not None and saved.updated_at == saved_time
        assert saved.anchor == screen.reading_anchor
        assert screen.session.page_map is not None
        assert screen.session.page_map.locate(saved.anchor) == screen.current_page


async def test_complete_network_loss_restores_cached_local_position_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """tmp_path/monkeypatch 隔离目录与终端；所有 HTTP 失败时仍自动恢复已缓存本地书页。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(terminal, "Image", TGPRenderable)
    scenario = CloudScenario(tmp_path, "cloud-new", 100, 150)
    async with selected_book(scenario):
        saved = scenario.store.load("reader", "1")
        assert saved is not None and saved.chapter_uid == "3"
    scenario.mode = "offline-all"
    async with selected_book(scenario) as (_, _, screen):
        assert screen.document.from_cache
        assert screen.document.chapter.uid == "3"
        assert screen.reading_anchor == saved.anchor
        assert "读取失败" in screen.position_notice and "本地" in screen.position_notice
        assert scenario.store.load("reader", "1") == saved


@pytest.mark.parametrize("mode,local_time", [("cloud-new", 100), ("local-new", 200)])
async def test_reopening_graphics_uses_latest_local_position(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, local_time: float,
) -> None:
    """隔离系统边界；首次续读之后的新记录不得被再次打开图形视图覆盖。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(terminal, "Image", TGPRenderable)
    scenario = CloudScenario(tmp_path, mode, local_time, 150)
    async with selected_book(scenario) as (app, pilot, screen):
        target = "你好" if mode == "cloud-new" else "微信"
        anchor = prepare_document(screen.document.source).anchor_from_source(screen.document.source.index(target))
        latest = scenario.store.save(screen.document, anchor, updated_at=300)
        await pilot.press("escape", "g")
        await pilot.pause()
        await asyncio.wait_for(app.workers.wait_for_complete(), 15)
        reopened = app.screen
        assert isinstance(reopened, GraphicsScreen)
        assert reopened.reading_anchor == latest.anchor
        assert scenario.store.load("reader", "1") == latest
