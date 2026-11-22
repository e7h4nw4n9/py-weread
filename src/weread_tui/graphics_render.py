"""受控子进程排版会话，界面只持有图片路径。"""

import asyncio
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any


class RenderError(Exception):
    """图形排版未完成，可向用户显示的原因。"""


class RenderSession:
    """一个章节的 PDF 和邻页缓存，原生渲染按任务串行执行。"""

    def __init__(self, cache_root: Path) -> None:
        """cache_root 保存当前会话的临时分页文件，关闭后清理。"""
        cache_root.mkdir(parents=True, exist_ok=True)
        self._temporary = TemporaryDirectory(prefix="pages-", dir=cache_root)
        self.directory = Path(self._temporary.name)
        self.page_count = 0
        self._lock = asyncio.Lock()
        self._process: asyncio.subprocess.Process | None = None

    async def _stop_process(self, process: asyncio.subprocess.Process) -> None:
        """终止并回收 process，取消不让原生渲染继续消耗资源。"""
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await process.wait()

    async def _run(self, job: dict[str, Any]) -> dict[str, Any]:
        """执行 job；communicate 同时排空输出，避免子进程日志堵塞。"""
        async with self._lock:
            environment = os.environ.copy()
            if sys.platform == "darwin":
                libraries = [path for path in ("/opt/homebrew/lib", "/usr/local/lib") if Path(path).is_dir()]
                existing = environment.get("DYLD_FALLBACK_LIBRARY_PATH", "")
                environment["DYLD_FALLBACK_LIBRARY_PATH"] = os.pathsep.join(filter(None, [existing, *libraries]))
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "weread_tui.render_worker",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env=environment,
            )
            self._process = process
            try:
                output, _ = await process.communicate(json.dumps({"directory": str(self.directory), **job}).encode())
                if not output:
                    raise RenderError("图形渲染进程异常退出，请重新打开章节。")
                result: dict[str, Any] = json.loads(output)
                if process.returncode or "error" in result:
                    raise RenderError(result.get("error", "图形渲染未完成。"))
                return result
            finally:
                await self._stop_process(process)
                self._process = None

    async def paginate(self, source: str, *, stylesheet: str, images: dict[str, Path], width: int, height: int) -> int:
        """按 source、stylesheet、images 和 width/height 像素视口分页。"""
        result = await self._run({
            "action": "paginate", "source": source, "stylesheet": stylesheet,
            "images": {reference: str(path) for reference, path in images.items()}, "width": width, "height": height,
        })
        self.page_count = int(result["page_count"])
        return self.page_count

    async def page(self, index: int) -> Path:
        """返回 index 页的已完成图片，复用本会话已生成的页面。"""
        if not 0 <= index < self.page_count:
            raise RenderError("页码不在当前章节范围内。")
        path = self.directory / f"page-{index}.png"
        if not path.is_file():
            await self._run({"action": "rasterize", "index": index})
        return path

    def keep_near(self, index: int) -> None:
        """仅保留 index 及相邻页，限制一次阅读会话的栅格缓存。"""
        for path in self.directory.glob("page-*.png"):
            page_index = int(path.stem.split("-")[1])
            if abs(page_index - index) > 1:
                path.unlink(missing_ok=True)

    async def close(self) -> None:
        """停止当前任务并清理该会话的 PDF 与图片。"""
        if self._process is not None:
            await self._stop_process(self._process)
        self._temporary.cleanup()
