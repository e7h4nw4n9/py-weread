"""书架与层级目录界面。"""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from weread_tui.account import AccountService
from weread_tui.provider import AuthenticationExpired, Book, Chapter, ProviderError, WereadProvider
from weread_tui.reader import ReaderScreen
from weread_tui.progress import ProgressStore


class NavigationList(ListView):
    """为书架和目录列表增加 Vim 风格移动键。"""

    BINDINGS = [Binding("j", "cursor_down", show=False), Binding("k", "cursor_up", show=False)]


class LibraryScreen(Screen[None]):
    """选书后异步显示目录，关闭界面时取消其网络请求。"""

    BINDINGS = [
        Binding("escape,q", "back", "返回"),
        Binding("r", "refresh", "刷新书架"),
        Binding("question_mark", "help", "帮助"),
    ]
    DEFAULT_CSS = """
    LibraryScreen #library-status { height: auto; padding: 1; }
    LibraryScreen #library-body { height: 1fr; }
    LibraryScreen #books { width: 45%; border: solid $accent; }
    LibraryScreen #chapters { width: 55%; border: solid $accent; }
    LibraryScreen Label { padding: 0 1; }
    """

    def __init__(self, account: AccountService, progress: ProgressStore | None = None) -> None:
        """account 提供账号边界，progress 提供按账号隔离的阅读位置。"""
        super().__init__()
        self.account = account
        self.progress = progress
        self.provider = WereadProvider(account.credentials, account.transport)
        self.books: list[Book] = []
        self.chapters: list[Chapter] = []
        self.selected_book: Book | None = None

    def compose(self) -> ComposeResult:
        """生成左右分栏的书架与目录。"""
        yield Header()
        yield Static("正在读取书架…", id="library-status", markup=False)
        with Horizontal(id="library-body"):
            yield NavigationList(id="books")
            yield NavigationList(id="chapters")
        yield Footer()

    def on_mount(self) -> None:
        """启动当前账号的书架读取。"""
        self.action_refresh()
        self.query_one("#books", ListView).focus()

    def action_refresh(self) -> None:
        """刷新前取消旧目录请求，避免已清空的书籍被旧响应填回。"""
        self.workers.cancel_group(self, "catalogue")
        self.run_worker(self.load_books(), group="shelf", exclusive=True)

    async def load_books(self) -> None:
        """读取并展示书架，包括明确标记为暂不支持的非电子书条目。"""
        shelf = self.query_one("#books", ListView)
        await shelf.clear()
        await self.query_one("#chapters", ListView).clear()
        self.books = []
        self.chapters = []
        self.show_status("正在读取书架…")
        try:
            self.books = await self.provider.get_bookshelf()
            for index, book in enumerate(self.books, 1):
                suffix = "" if book.kind == "book" else "（暂不支持阅读）"
                await shelf.append(ListItem(Label(f"{index}. {book.title} — {book.author}{suffix}", markup=False)))
            self.show_status(f"书架共 {len(self.books)} 个条目；Enter 选书，Tab 切换书架和目录。")
        except ProviderError as error:
            self.show_error(error)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """event 指定当前选中的书架行。"""
        if event.list_view.id == "books":
            self.run_worker(self.load_chapters(self.books[event.index]), group="catalogue", exclusive=True)
        elif event.list_view.id == "chapters" and self.selected_book is not None:
            self.app.push_screen(ReaderScreen(self.provider, self.account, self.selected_book,
                                             self.chapters[event.index], self.chapters, progress=self.progress))

    async def load_chapters(self, book: Book) -> None:
        """获取 book 的目录，后一次选书取消前一次请求。"""
        catalogue = self.query_one("#chapters", ListView)
        await catalogue.clear()
        self.chapters = []
        self.selected_book = None
        self.show_status(f"正在读取《{book.title}》目录…")
        try:
            self.chapters = await self.provider.get_chapters(book)
            self.selected_book = book
            for chapter in self.chapters:
                indent = "  " * max(0, chapter.level - 1)
                await catalogue.append(ListItem(Label(f"{indent}{chapter.title}", markup=False)))
            self.show_status(f"《{book.title}》共 {len(self.chapters)} 章；Tab 切换目录，Enter 阅读章节。")
            if self.progress is not None:
                saved = self.progress.load(book.account_id, book.book_id)
                if saved is not None:
                    saved_chapter = next((chapter for chapter in self.chapters if chapter.uid == saved.chapter_uid), None)
                    if saved_chapter is None:
                        self.show_status("保存的章节已不在目录中，无法恢复本地位置；旧记录已保留。")
                    else:
                        self.app.push_screen(ReaderScreen(self.provider, self.account, book, saved_chapter, self.chapters,
                                                         progress=self.progress, resume=True))
        except ProviderError as error:
            self.show_error(error)

    def show_status(self, message: str) -> None:
        """显示 message，升级提示不阻断当前业务数据。"""
        if self.provider.upgrade_available:
            message += " 官方接口有版本更新提示，当前数据仍可使用。"
        self.query_one("#library-status", Static).update(message)

    def show_error(self, error: ProviderError) -> None:
        """显示 error；仅在明确鉴权失败时更新密钥状态。"""
        if isinstance(error, AuthenticationExpired):
            self.account.key_status = "已失效" if self.account.credentials.api_key else "未提供"
        self.show_status(str(error))

    def action_back(self) -> None:
        """回到首页，后台请求随界面移除而取消。"""
        self.app.pop_screen()

    def action_help(self) -> None:
        """显示当前界面的导航键位。"""
        self.notify("j/k 或方向键移动，Enter 选书，Tab 切换列表，r 刷新，Esc 或 q 返回。")
