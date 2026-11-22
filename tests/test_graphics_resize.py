"""从真实界面和原生输出验证窗口重排。"""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
import sys
from typing import Any

import httpx
from PIL import Image
import pytest
from textual import events
from textual.pilot import Pilot
from textual_image._terminal import CellSize, get_cell_size

from weread_tui.account import AccountService
from weread_tui.app import WereadApp
from weread_tui.credentials import Credentials, CredentialStore
from weread_tui.graphics_screen import GraphicsScreen
from weread_tui.models import Book, Chapter, ChapterDocument
from weread_tui.provider import WereadProvider
from weread_tui.terminal import TerminalCapabilities
from weread_tui import terminal


@pytest.fixture
async def reading_view(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[WereadApp, Pilot[None], GraphicsScreen]]:
    """tmp_path 隔离资源；monkeypatch 固定终端像素缓存，HTTP 使用测试响应。"""
    monkeypatch.setattr(get_cell_size, "_result", CellSize(8, 16), raising=False)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    app = WereadApp(account=AccountService(CredentialStore(), transport=transport))
    provider = WereadProvider(Credentials(vid="reader", skey="session"), transport, tmp_path / "resources")
    document = ChapterDocument(Book("reader", "1", "书"), Chapter("2", "长章"), "重复文字 English。" * 400, "txt")
    async with app.run_test(size=(80, 30)) as pilot:
        screen = GraphicsScreen(provider, document, TerminalCapabilities(8, 16, True))
        await app.push_screen(screen)
        await pilot.pause()
        await asyncio.wait_for(app.workers.wait_for_complete(), 15)
        yield app, pilot, screen


async def test_resize_updates_real_page_and_keeps_character(
    reading_view: tuple[WereadApp, Pilot[None], GraphicsScreen],
) -> None:
    """reading_view 提供正在阅读的界面，缩放后图像大小与新视口一致。"""
    app, pilot, screen = reading_view
    await pilot.press("l")
    await asyncio.wait_for(app.workers.wait_for_complete(), 15)
    anchor = screen.reading_anchor
    assert anchor is not None
    previous = screen.session.directory
    await pilot.resize_terminal(100, 40)
    await asyncio.wait_for(app.workers.wait_for_complete(), 15)
    path = screen.picture.image
    assert isinstance(path, Path)
    area = screen.query_one("#graphics-page").content_size
    with Image.open(path) as image:
        assert image.size == (area.width * 8, area.height * 16)
    assert screen.session.page_map is not None
    assert screen.session.page_map.locate(anchor) == screen.current_page
    assert not previous.exists()


async def test_pixel_density_change_with_same_grid_updates_page_and_image_cache(
    reading_view: tuple[WereadApp, Pilot[None], GraphicsScreen],
) -> None:
    """reading_view 接收同一字符网格的新像素尺寸，模拟终端字号或跨屏变化。"""
    app, pilot, screen = reading_view
    app.post_message(events.Resize.from_dimensions((80, 30), (1600, 1200)))
    await pilot.pause()
    await asyncio.wait_for(app.workers.wait_for_complete(), 15)
    path = screen.picture.image
    assert isinstance(path, Path)
    area = screen.query_one("#graphics-page").content_size
    with Image.open(path) as image:
        assert image.size == (1600, area.height * 40)
    assert get_cell_size() == CellSize(20, 40)


async def test_pixel_size_poll_detects_changes_without_resize_event(
    reading_view: tuple[WereadApp, Pilot[None], GraphicsScreen], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """reading_view 没有收到 Resize；monkeypatch 模拟跨屏后 ioctl 返回新像素。"""
    app, pilot, screen = reading_view
    monkeypatch.setattr(terminal, "get_tiocgwinsz", lambda: (30, 80, 960, 720))
    await pilot.pause(0.6)
    await asyncio.wait_for(app.workers.wait_for_complete(), 15)
    path = screen.picture.image
    assert isinstance(path, Path)
    area = screen.query_one("#graphics-page").content_size
    with Image.open(path) as image:
        assert image.size == (960, area.height * 24)
    assert get_cell_size() == CellSize(12, 24)


async def test_rapid_settings_only_launch_final_render(
    reading_view: tuple[WereadApp, Pilot[None], GraphicsScreen], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """reading_view 连续调整字号；monkeypatch 统计真实外部进程，验证防抖效果。"""
    app, pilot, screen = reading_view
    spawn = asyncio.create_subprocess_exec
    children: list[asyncio.subprocess.Process] = []

    async def tracked_spawn(*command: str, **options: Any) -> asyncio.subprocess.Process:
        """按 command/options 启动真实子进程并记录其退出状态。"""
        process = await spawn(*command, **options)
        children.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", tracked_spawn)
    for key in ("plus", "plus", "minus", "plus"):
        app.simulate_key(key)
    await pilot.pause()
    await asyncio.wait_for(app.workers.wait_for_complete(), 15)
    assert screen.rendered_settings == (24, 1.6)
    assert len(children) == 1
    assert children[0].returncode == 0


@pytest.mark.parametrize("partial_output", [False, True])
async def test_native_crash_keeps_current_page_and_next_attempt_recovers(
    reading_view: tuple[WereadApp, Pilot[None], GraphicsScreen], monkeypatch: pytest.MonkeyPatch,
    partial_output: bool,
) -> None:
    """reading_view 保留可读页；monkeypatch 模拟崩溃，partial_output 决定是否输出半截结果。"""
    app, pilot, screen = reading_view
    spawn = asyncio.create_subprocess_exec
    children: list[asyncio.subprocess.Process] = []
    previous = screen.session

    async def crash_once(*command: str, **options: Any) -> asyncio.subprocess.Process:
        """第一次 command 用异常退出进程代替，后续 options 交给真实渲染器。"""
        if not children:
            program = 'print("{"); raise SystemExit(1)' if partial_output else "raise SystemExit(1)"
            process = await spawn(sys.executable, "-c", program, **options)
        else:
            process = await spawn(*command, **options)
        children.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", crash_once)
    await pilot.press("plus")
    await asyncio.wait_for(app.workers.wait_for_complete(), 15)
    assert screen.session is previous
    assert screen.picture.image is not None
    assert screen.rendered_settings == (20, 1.6)
    assert not screen.rendering
    await pilot.press("plus")
    await asyncio.wait_for(app.workers.wait_for_complete(), 15)
    assert screen.rendered_settings == (22, 1.6)
    assert screen.session is not previous
    assert not previous.directory.exists()
    assert all(child.returncode is not None for child in children)


@pytest.mark.parametrize("old_error", [False, True])
async def test_late_result_cannot_replace_new_settings(
    reading_view: tuple[WereadApp, Pilot[None], GraphicsScreen], monkeypatch: pytest.MonkeyPatch, old_error: bool,
) -> None:
    """reading_view 启动新排版；monkeypatch 延迟旧外部响应，old_error 区分旧结果与旧错误。"""
    app, pilot, screen = reading_view
    spawn = asyncio.create_subprocess_exec
    old_ready, release_old, latest_ready = asyncio.Event(), asyncio.Event(), asyncio.Event()
    children: list[asyncio.subprocess.Process] = []

    async def delayed_output(*command: str, **options: Any) -> asyncio.subprocess.Process:
        """command/options 交给真实进程，仅模拟外部完成通知在取消后才到达。"""
        process = await spawn(*command, **options)
        first = not children
        children.append(process)
        communicate = process.communicate

        async def receive(input: bytes | None = None) -> tuple[bytes, bytes]:
            """向原生进程发送 input，旧响应延迟到新版页面已经显示之后。"""
            result = await communicate(input)
            if first:
                old_ready.set()
                try:
                    await release_old.wait()
                except asyncio.CancelledError:
                    await release_old.wait()
                if old_error:
                    return b'{"error":"late failure"}', b""
            else:
                latest_ready.set()
            return result

        monkeypatch.setattr(process, "communicate", receive)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_output)
    try:
        await pilot.press("plus")
        await asyncio.wait_for(old_ready.wait(), 10)
        await pilot.press("plus")
        await asyncio.wait_for(latest_ready.wait(), 10)
        await pilot.pause()
        assert screen.rendered_settings == (24, 1.6)
        latest = screen.session
        release_old.set()
        await asyncio.wait_for(app.workers.wait_for_complete(), 15)
        assert screen.rendered_settings == (24, 1.6)
        assert screen.font_size == 24
        assert screen.session is latest
        assert screen.pending is None
        assert list((screen.provider.cache_root.parent / "render").iterdir()) == [latest.directory]
        assert all(child.returncode is not None for child in children)
    finally:
        release_old.set()


async def test_close_during_debounce_does_not_launch_process(
    reading_view: tuple[WereadApp, Pilot[None], GraphicsScreen], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """reading_view 刚收到设置就返回；monkeypatch 确保取消后不启动系统进程。"""
    app, pilot, screen = reading_view

    async def forbidden_spawn(*command: str, **options: Any) -> asyncio.subprocess.Process:
        """command/options 代表不应发生的原生启动。"""
        raise AssertionError("已关闭的阅读界面不能启动渲染进程")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden_spawn)
    app.simulate_key("plus")
    app.simulate_key("escape")
    await pilot.pause()
    await asyncio.wait_for(app.workers.wait_for_complete(), 10)
    assert screen.picture.image is None
    assert not list((screen.provider.cache_root.parent / "render").iterdir())


async def test_late_neighbour_page_cannot_delete_current_image(
    reading_view: tuple[WereadApp, Pilot[None], GraphicsScreen], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """reading_view 已翻到新位置时，monkeypatch 延迟的旧邻页完成不能清理新图像。"""
    app, pilot, screen = reading_view
    spawn = asyncio.create_subprocess_exec
    old_ready, release_old = asyncio.Event(), asyncio.Event()
    first = True

    async def delay_first_output(*command: str, **options: Any) -> asyncio.subprocess.Process:
        """只延迟首个 command 的输出交付，后续 options 保持正常渲染。"""
        nonlocal first
        process = await spawn(*command, **options)
        if first:
            first = False
            communicate = process.communicate

            async def receive(input: bytes | None = None) -> tuple[bytes, bytes]:
                """input 交给旧邻页进程，完成结果在新页面显示后交付。"""
                result = await communicate(input)
                old_ready.set()
                while not release_old.is_set():
                    try:
                        await release_old.wait()
                    except asyncio.CancelledError:
                        pass
                return result

            monkeypatch.setattr(process, "communicate", receive)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delay_first_output)
    try:
        await pilot.press("l")
        await asyncio.wait_for(old_ready.wait(), 10)
        await pilot.press("plus")

        async def wait_for_reflow() -> None:
            """新排版使用独立会话，不等待旧邻页的延迟响应。"""
            while screen.rendered_settings != (22, 1.6):
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait_for_reflow(), 10)
        target = screen.current_page + 3
        await pilot.press("l", "l", "l")

        async def wait_for_page() -> None:
            """等最新翻页完成，不等待仍被延迟的旧邻页任务。"""
            while screen.current_page != target:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait_for_page(), 10)
        release_old.set()
        await asyncio.wait_for(app.workers.wait_for_complete(), 15)
        path = screen.picture.image
        assert isinstance(path, Path)
        assert path.is_file()
        assert screen.current_page == target
    finally:
        release_old.set()
