"""以整页图像显示 XHTML/CSS 排版结果。"""

from html import escape

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Static
from textual_image.widget import Image

from weread_tui.chapter_protocol import ContentError
from weread_tui.graphics_render import RenderError, RenderSession
from weread_tui.models import ChapterDocument
from weread_tui.provider import ProviderError, WereadProvider
from weread_tui.terminal import TerminalCapabilities


class GraphicsScreen(Screen[None]):
    """分页与栅格化在子进程执行，界面只读取已完成的页面图片。"""

    BINDINGS = [
        Binding("escape,q", "back", "返回文字"),
        Binding("l,right,pagedown,space", "next_page", "下一页"),
        Binding("h,left,pageup", "previous_page", "上一页"),
    ]
    DEFAULT_CSS = """
    GraphicsScreen #graphics-status { height: auto; padding: 0 1; }
    GraphicsScreen #graphics-page { height: 1fr; width: 100%; align: center middle; }
    GraphicsScreen Image { width: auto; height: auto; max-width: 100%; max-height: 100%; }
    """

    def __init__(self, provider: WereadProvider, document: ChapterDocument, capabilities: TerminalCapabilities) -> None:
        """provider/document 提供已授权正文，capabilities 是终端系统边界。"""
        super().__init__()
        self.provider = provider
        self.document = document
        self.capabilities = capabilities
        self.session = RenderSession(provider.cache_root.parent / "render")
        self.picture = Image(id="page-image")
        self.target_page = 0
        self.current_page = 0

    def compose(self) -> ComposeResult:
        """生成页图像区域及加载状态。"""
        yield Header()
        yield Static("正在进行图形排版…", id="graphics-status", markup=False)
        with Vertical(id="graphics-page"):
            yield self.picture
        yield Footer()

    def on_mount(self) -> None:
        """等待首次布局完成，再测量可用阅读区域。"""
        self.call_after_refresh(self.start_render)

    def start_render(self) -> None:
        """在后台生成当前章节的 PDF 和首张图片。"""
        self.run_worker(self.render_chapter(), group="graphics", exclusive=True)

    async def render_chapter(self) -> None:
        """使用当前视口、原书样式与已获取图片生成书页。"""
        area = self.query_one("#graphics-page").content_size
        try:
            resources = await self.provider.get_images(self.document)
            stylesheet = ""
            try:
                stylesheet = await self.provider.get_stylesheet(self.document)
            except ContentError:
                self.notify("原书样式暂不可用，采用默认排版。")
            source = self.document.source
            if self.document.format == "txt":
                source = f'<html><body><div style="white-space:pre-wrap">{escape(source)}</div></body></html>'
            await self.session.paginate(
                source, stylesheet=stylesheet, images=resources.paths,
                width=area.width * self.capabilities.cell_width,
                height=area.height * self.capabilities.cell_height,
            )
            if resources.missing:
                self.notify(f"{len(resources.missing)} 张图片不可用，图形页使用可用资源。")
            await self.show_page(0)
        except (ContentError, ProviderError, RenderError) as error:
            self.query_one("#graphics-status", Static).update(f"图形排版未完成：{error}")

    async def show_page(self, index: int) -> None:
        """显示 index 页，并在后台准备相邻页。"""
        try:
            path = await self.session.page(index)
            self.picture.image = path
            self.current_page = index
            self.query_one("#graphics-status", Static).update(
                f"{self.document.chapter.title} · 第 {index + 1}/{self.session.page_count} 页"
            )
            for neighbour in (index - 1, index + 1):
                if 0 <= neighbour < self.session.page_count:
                    await self.session.page(neighbour)
            self.session.keep_near(index)
        except RenderError as error:
            self.query_one("#graphics-status", Static).update(f"书页生成失败：{error}")

    def move_page(self, offset: int) -> None:
        """按 offset 调整目标页，快速按键只保留最新目标。"""
        target = self.target_page + offset
        if not self.session.page_count:
            self.notify("正在排版，请稍候。")
            return
        if 0 <= target < self.session.page_count:
            self.target_page = target
            self.run_worker(self.show_page(target), group="graphics", exclusive=True)

    def action_next_page(self) -> None:
        """显示下一页。"""
        self.move_page(1)

    def action_previous_page(self) -> None:
        """显示上一页。"""
        self.move_page(-1)

    def action_back(self) -> None:
        """返回文字阅读界面。"""
        self.app.pop_screen()

    async def on_unmount(self) -> None:
        """清理终端图像、原生子进程及当前会话的临时文件。"""
        self.picture.image = None
        await self.session.close()
