"""通过真实渲染子进程验证分页和像素尺寸。"""

from pathlib import Path
import asyncio
import sys
from typing import Any

from PIL import Image
import pytest

from weread_tui.graphics_render import RenderSession


async def test_graphic_pages_fit_requested_viewport(tmp_path: Path) -> None:
    """tmp_path 隔离图形缓存，固定分页样本应生成两张精确尺寸书页。"""
    session = RenderSession(tmp_path)
    try:
        count = await session.paginate(
            '<html><body><p>第一页 First page</p><p style="break-before:page">第二页 Second page</p></body></html>',
            stylesheet="", images={}, width=480, height=640,
        )
        assert count == 2
        for index in range(count):
            path = await session.page(index)
            with Image.open(path) as image:
                assert image.size == (480, 640)
    finally:
        await session.close()


async def test_cancelling_render_reaps_native_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """tmp_path 隔离缓存，monkeypatch 只替换外部进程以稳定复现长任务取消。"""
    spawn = asyncio.create_subprocess_exec
    started = asyncio.Event()
    children: list[asyncio.subprocess.Process] = []

    async def slow_process(*command: str, **options: Any) -> asyncio.subprocess.Process:
        """command 由被测服务生成；options 保留真实管道与环境设置。"""
        process = await spawn(sys.executable, "-c", "import sys,time; sys.stdin.read(); time.sleep(60)", **options)
        children.append(process)
        started.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", slow_process)
    session = RenderSession(tmp_path)
    task = asyncio.create_task(session.paginate("<p>text</p>", stylesheet="", images={}, width=480, height=640))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert children[0].returncode is not None
    finally:
        await session.close()


@pytest.mark.parametrize("cancel_count", [1, 2])
async def test_cancelling_during_process_creation_reaps_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel_count: int,
) -> None:
    """tmp_path 隔离会话；monkeypatch 延迟句柄，cancel_count 模拟连续任务取消。"""
    spawn = asyncio.create_subprocess_exec
    started = asyncio.Event()
    release = asyncio.Event()
    children: list[asyncio.subprocess.Process] = []

    async def delayed_spawn(*command: str, **options: Any) -> asyncio.subprocess.Process:
        """command/options 来自真实渲染调用，只延迟系统进程句柄的返回。"""
        process = await spawn(sys.executable, "-c", "import time; time.sleep(60)", **options)
        children.append(process)
        started.set()
        await release.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)
    session = RenderSession(tmp_path)
    task = asyncio.create_task(session.paginate("<p>文本</p>", stylesheet="", images={}, width=480, height=640))
    try:
        await asyncio.wait_for(started.wait(), 2)
        for _ in range(cancel_count):
            task.cancel()
            await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert children[0].returncode is not None
    finally:
        for child in children:
            if child.returncode is None:
                child.kill()
            await child.wait()
        await session.close()


@pytest.mark.parametrize("approved", [True, False])
async def test_renderer_only_reads_explicit_image_mapping(tmp_path: Path, approved: bool) -> None:
    """tmp_path 保存红色样本图；approved 决定是否显式提供给排版器。"""
    path = tmp_path / "private-picture.png"
    Image.new("RGB", (100, 100), "red").save(path)
    reference = "image.png" if approved else path.as_uri()
    session = RenderSession(tmp_path / "render")
    try:
        await session.paginate(
            f'<html><body><img src="{reference}" style="position:fixed;left:0;top:0;width:100px;height:100px"></body></html>',
            stylesheet="", images={reference: path} if approved else {}, width=480, height=640,
        )
        with Image.open(await session.page(0)) as rendered:
            pixel = rendered.getpixel((50, 50))
            assert isinstance(pixel, tuple)
            assert (pixel[:3] == (255, 0, 0)) is approved
    finally:
        await session.close()
