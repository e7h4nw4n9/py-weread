"""以整页图像显示 XHTML/CSS 排版结果。"""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Static
from textual_image.widget import Image

from weread_tui.chapter_protocol import ContentError
from weread_tui.graphics_render import RenderError, RenderSession
from weread_tui.positions import ContentAnchor, PositionError
from weread_tui.source_positions import prepare_document
from weread_tui.resources import ImageResources
from weread_tui.models import ChapterDocument
from weread_tui.provider import ProviderError, WereadProvider
from weread_tui.terminal import TerminalCapabilities


class GraphicsScreen(Screen[None]):
    """分页与栅格化在子进程执行，界面只读取已完成的页面图片。"""

    BINDINGS = [
        Binding("escape,q", "back", "返回文字"),
        Binding("l,right,pagedown,space", "next_page", "下一页"),
        Binding("h,left,pageup", "previous_page", "上一页"),
        Binding("plus,equals_sign,shift+plus,shift+equals_sign", "font(2)", "增大字号", key_display="+"),
        Binding("minus", "font(-2)", "缩小字号", key_display="-"),
        Binding("shift+up", "spacing(0.2)", "增大行距", key_display="Shift+↑"),
        Binding("shift+down", "spacing(-0.2)", "缩小行距", key_display="Shift+↓"),
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
        self.pending: RenderSession | None = None
        self.prepared = prepare_document(document.source, document.format)
        self.resources: ImageResources | None = None
        self.stylesheet: str | None = None
        self.font_size = 20
        self.line_height = 1.6
        self.rendered_settings = (20, 1.6)
        self.picture = Image(id="page-image")
        self.target_page = 0
        self.current_page = 0
        self.reading_anchor: ContentAnchor | None = None

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

    async def render_chapter(self, anchor: ContentAnchor | None = None) -> None:
        """按当前字号和行高重排；anchor 指向重排前正在显示的字符。"""
        area = self.query_one("#graphics-page").content_size
        font_size, line_height = self.font_size, self.line_height
        pending = RenderSession(self.provider.cache_root.parent / "render")
        self.pending = pending
        try:
            if self.resources is None:
                self.resources = await self.provider.get_images(self.document)
                if self.resources.missing:
                    self.notify(f"{len(self.resources.missing)} 张图片不可用，图形页使用可用资源。")
            if self.stylesheet is None:
                try:
                    self.stylesheet = await self.provider.get_stylesheet(self.document)
                except ContentError:
                    self.stylesheet = ""
                    self.notify("原书样式暂不可用，采用默认排版。")
            await pending.paginate(
                self.prepared, stylesheet=self.stylesheet, images=self.resources.paths,
                width=area.width * self.capabilities.cell_width,
                height=area.height * self.capabilities.cell_height,
                font_size=font_size, line_height=line_height,
            )
            assert pending.page_map is not None
            index = pending.page_map.locate(anchor) if anchor is not None else 0
            path = await pending.page(index)
            previous = self.session
            self.session = pending
            self.picture.image = path
            self.current_page = self.target_page = index
            # 连续重排沿用同一个字符，只有主动翻页才重新选取锚点。
            self.reading_anchor = anchor if anchor is not None else pending.page_map.first_anchor(index)
            self.rendered_settings = (font_size, line_height)
            self.update_status()
            await previous.close()
        except (ContentError, ProviderError, RenderError, PositionError) as error:
            self.font_size, self.line_height = self.rendered_settings
            self.query_one("#graphics-status", Static).update(f"图形排版未完成：{error}")
        finally:
            if pending is not self.session:
                await pending.close()
            if self.pending is pending:
                self.pending = None

    def update_status(self) -> None:
        """显示实际书页及本次排版参数。"""
        font_size, line_height = self.rendered_settings
        self.query_one("#graphics-status", Static).update(
            f"{self.document.chapter.title} · 第 {self.current_page + 1}/{self.session.page_count} 页"
            f" · 字号 {font_size} · 行距 {line_height:.1f}"
        )

    def reflow(self) -> None:
        """沿用当前阅读锚点连续重排，不复用旧页码或重新选取页首。"""
        page_map = self.session.page_map
        if page_map is None:
            return
        anchor = self.reading_anchor
        if anchor is None:
            self.notify("当前页没有可定位的正文，无法保留位置。")
            return
        self.query_one("#graphics-status", Static).update(
            f"正在重排 · 目标字号 {self.font_size} · 行距 {self.line_height:.1f}"
        )
        self.run_worker(self.render_chapter(anchor), group="graphics", exclusive=True)

    def action_font(self, offset: int) -> None:
        """按 offset 调整字号，范围为 16 至 28 像素。"""
        if self.session.page_map is None:
            self.notify("正在完成首次排版，请稍候。")
            return
        target = min(28, max(16, self.font_size + offset))
        if target == self.font_size:
            self.notify("字号范围为 16–28，已达到边界。")
            return
        self.font_size = target
        self.reflow()

    def action_spacing(self, offset: float) -> None:
        """按 offset 调整行高倍数，范围为 1.2 至 2.0。"""
        if self.session.page_map is None:
            self.notify("正在完成首次排版，请稍候。")
            return
        target = round(min(2.0, max(1.2, self.line_height + offset)), 1)
        if target == self.line_height:
            self.notify("行距范围为 1.2–2.0，已达到边界。")
            return
        self.line_height = target
        self.reflow()

    async def show_page(self, index: int) -> None:
        """显示 index 页，并在后台准备相邻页。"""
        try:
            path = await self.session.page(index)
            self.picture.image = path
            self.current_page = index
            assert self.session.page_map is not None
            self.reading_anchor = self.session.page_map.first_anchor(index)
            self.update_status()
            for neighbour in (index - 1, index + 1):
                if 0 <= neighbour < self.session.page_count:
                    await self.session.page(neighbour)
            self.session.keep_near(index)
        except RenderError as error:
            self.query_one("#graphics-status", Static).update(f"书页生成失败：{error}")

    def move_page(self, offset: int) -> None:
        """按 offset 调整目标页，快速按键只保留最新目标。"""
        if self.pending is not None:
            self.notify("正在重排，请稍候。")
            return
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
        if self.pending is not None:
            await self.pending.close()
        await self.session.close()
