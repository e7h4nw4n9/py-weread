"""诊断阅读视图；原始正文保留在文档中，显示转换不回写原文。"""

import sqlite3

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Static
from textual_image.widget import AutoImage, Image, SixelImage

from weread_tui.account import AccountService
from weread_tui.chapter_protocol import ContentError
from weread_tui.provider import Book, Chapter, ChapterDocument, ProviderError, WereadProvider
from weread_tui.web_reader import WebSessionExpired
from weread_tui.document import ImageReference, parse_content
from weread_tui.graphics_screen import GraphicsScreen
from weread_tui.terminal import terminal_capabilities
from weread_tui.progress import ProgressStore
from weread_tui.source_positions import prepare_document
from weread_tui.positions import PositionError


class ReaderScreen(Screen[None]):
    """按需获取一个真实章节，后台请求可随返回操作取消。"""

    BINDINGS = [
        Binding("escape,q", "back", "返回目录"),
        Binding("r", "reload", "重新加载"),
        Binding("j", "line_down", "向下", show=False),
        Binding("k", "line_up", "向上", show=False),
        Binding("left_square_bracket", "previous_chapter", "上一章"),
        Binding("right_square_bracket", "next_chapter", "下一章"),
        Binding("g", "graphics", "图形排版"),
    ]
    DEFAULT_CSS = """
    ReaderScreen #reading-status { height: auto; padding: 1; }
    ReaderScreen #reading-content { width: 90%; max-width: 100; height: auto; margin: 1 2; }
    ReaderScreen #reading-scroll { height: 1fr; }
    ReaderScreen #reading-content Static { height: auto; }
    ReaderScreen .image-slot { height: auto; width: 100%; margin: 1 0; }
    ReaderScreen Image { width: auto; max-width: 100%; height: auto; }
    """

    def __init__(self, provider: WereadProvider, account: AccountService, book: Book,
                 chapter: Chapter, chapters: list[Chapter] | None = None,
                 progress: ProgressStore | None = None, resume: bool = False) -> None:
        """provider/account 提供账号，book/chapter/chapters 指定内容，progress/resume 控制本地恢复。"""
        super().__init__()
        self.provider = provider
        self.account = account
        self.book = book
        self.chapter = chapter
        self.chapters = chapters if chapters is not None else [chapter]
        self.chapter_index = self.chapters.index(chapter)
        self.document: ChapterDocument | None = None
        self.images: list[AutoImage | SixelImage] = []
        self.progress = progress
        self.remember_chapter = not resume
        self.resume_graphics = resume

    def compose(self) -> ComposeResult:
        """生成章节正文区和操作提示。"""
        yield Header()
        yield Static(f"《{self.book.title}》 · {self.chapter.title}", id="reading-status", markup=False)
        with VerticalScroll(id="reading-scroll"):
            yield Vertical(Static("正在读取正文…", markup=False), id="reading-content")
        yield Footer()

    def on_mount(self) -> None:
        """进入章节时启动后台读取。"""
        self.action_reload()

    def action_reload(self) -> None:
        """取消旧请求后重新读取当前章节。"""
        self.run_worker(self.load_chapter(), group="chapter", exclusive=True)

    async def load_chapter(self) -> None:
        """正文通过校验后才更新可见内容，失败保留明确提示。"""
        content = self.query_one("#reading-content", Vertical)
        self.clear_images()
        await content.remove_children()
        await content.mount(Static("正在读取正文…", markup=False))
        self.document = None
        try:
            document = await self.provider.get_chapter(self.book, self.chapter)
            blocks = parse_content(document)
            await content.remove_children()
            for index, block in enumerate(blocks):
                if isinstance(block, str):
                    await content.mount(Static(block, markup=False))
                else:
                    await content.mount(Vertical(Static(f"{block.alt}：正在加载图片…", markup=False),
                                                 id=f"image-{index}", classes="image-slot"))
            self.document = document
            if self.remember_chapter:
                if self.progress is not None:
                    try:
                        prepared = prepare_document(document.source, document.format)
                        self.progress.save(document, prepared.anchor_from_source(0))
                    except (PositionError, sqlite3.Error) as error:
                        self.notify(f"本地章节位置保存失败：{error}", severity="error")
                self.remember_chapter = False
            self.query_one("#reading-scroll", VerticalScroll).scroll_home(animate=False)
            resources = await self.provider.get_images(document)
            for index, block in enumerate(blocks):
                if isinstance(block, ImageReference):
                    slot = self.query_one(f"#image-{index}", Vertical)
                    await slot.remove_children()
                    path = resources.paths.get(block.src)
                    if path is not None:
                        image = Image(path)
                        self.images.append(image)
                        await slot.mount(image)
                    else:
                        await slot.mount(Static(f"[图片未加载：{block.alt}]", markup=False))
            title = f"《{self.book.title}》 · {self.chapter.title}"
            if resources.missing:
                title += f" · {len(resources.missing)} 张图片未加载。{resources.warning}"
            self.query_one("#reading-status", Static).update(title)
            if self.resume_graphics:
                self.resume_graphics = False
                self.action_graphics()
        except (ContentError, ProviderError) as error:
            if isinstance(error, WebSessionExpired):
                self.account.web_status = "已失效"
            self.clear_images()
            await content.remove_children()
            await content.mount(Static(str(error), markup=False))

    def clear_images(self) -> None:
        """通过公开 image 属性释放图像协议资源，不依赖卸载时的隐式清理。"""
        for image in self.images:
            image.image = None
        self.images.clear()

    def on_unmount(self) -> None:
        """关闭阅读界面时清理已发送给终端的图片。"""
        self.clear_images()

    def change_chapter(self, offset: int) -> None:
        """按 offset 切换目录位置，旧正文及图片请求由独占任务取消。"""
        index = self.chapter_index + offset
        if not 0 <= index < len(self.chapters):
            self.notify("已到章节边界。")
            return
        self.chapter_index = index
        self.chapter = self.chapters[index]
        self.remember_chapter = True
        self.query_one("#reading-status", Static).update(f"《{self.book.title}》 · {self.chapter.title}")
        self.action_reload()

    def action_previous_chapter(self) -> None:
        """切换到上一章。"""
        self.change_chapter(-1)

    def action_next_chapter(self) -> None:
        """切换到下一章。"""
        self.change_chapter(1)

    def action_graphics(self) -> None:
        """在支持图像协议的终端打开当前章节的整页排版。"""
        if self.document is None:
            self.notify("正文尚未加载完成。")
            return
        capabilities = terminal_capabilities()
        if not capabilities.graphics:
            self.notify("当前终端未探测到书页图像协议，继续使用文字阅读。")
            return
        self.app.push_screen(GraphicsScreen(self.provider, self.document, capabilities, progress=self.progress))

    def action_line_down(self) -> None:
        """正文向下滚动一行。"""
        self.query_one("#reading-scroll", VerticalScroll).scroll_down(animate=False)

    def action_line_up(self) -> None:
        """正文向上滚动一行。"""
        self.query_one("#reading-scroll", VerticalScroll).scroll_up(animate=False)

    def action_back(self) -> None:
        """返回目录；当前界面的后台请求同时取消。"""
        self.app.pop_screen()
