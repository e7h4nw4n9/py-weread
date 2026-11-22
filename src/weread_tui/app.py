"""阅读器基础界面。"""

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Button, Footer, Header, Static

from weread_tui.account import AccountService
from weread_tui.credentials import Credentials, CredentialStore
from weread_tui.login import ApiKeyScreen, LoginScreen
from weread_tui.library import LibraryScreen
from weread_tui.terminal import refresh_terminal_capabilities


class WereadApp(App[None]):
    """提供首页、键位帮助和退出操作。"""

    TITLE = "微信读书终端阅读器"
    BINDINGS = [
        Binding("q", "quit", "退出"),
        Binding("question_mark", "toggle_help", "帮助"),
    ]
    CSS = """
    Screen { align: center middle; }
    #welcome { width: 80%; height: auto; padding: 1 2; }
    #help { display: none; width: 80%; height: auto; padding: 1 2; }
    #account-status { width: 80%; height: auto; padding: 0 2; }
    #account-actions { width: auto; height: auto; }
    #account-actions Button { margin: 1; }
    """

    def __init__(self, account: AccountService | None = None) -> None:
        """account 提供 HTTP 与凭据存储边界，默认使用系统 Keychain。"""
        super().__init__()
        self.account = account if account is not None else AccountService(CredentialStore.system())

    def compose(self) -> ComposeResult:
        """生成首页内容与键位提示。"""
        yield Header()
        yield Static("微信读书终端阅读器\n\n尚未登录。\n按 ? 查看帮助，按 q 退出。", id="welcome")
        yield Static("正在读取登录状态…", id="account-status", markup=False)
        with Horizontal(id="account-actions"):
            yield Button("扫码登录", id="login", disabled=True)
            yield Button("补充 API Key", id="set-api-key", disabled=True)
            yield Button("打开书架", id="bookshelf", disabled=True)
        yield Static("键位帮助\n\n?：显示或隐藏帮助\nq：退出应用", id="help")
        yield Footer()

    def on_mount(self) -> None:
        """后台恢复凭据，避免 Keychain 和网络操作阻塞界面。"""
        self.run_worker(self.restore_account(), group="account")

    def on_resize(self, event: events.Resize) -> None:
        """用 event 更新全局像素缓存，覆盖尚未打开阅读界面的尺寸变化。"""
        refresh_terminal_capabilities(event.size, event.pixel_size)

    async def restore_account(self) -> None:
        """恢复完成后启用账号操作。"""
        await self.account.restore()
        self.update_account()
        self.enable_account_buttons(True)

    def enable_account_buttons(self, enabled: bool) -> None:
        """enabled 控制账号操作，避免保存期间启动另一次账号变更。"""
        self.query_one("#login", Button).disabled = not enabled
        self.query_one("#set-api-key", Button).disabled = not enabled
        self.query_one("#bookshelf", Button).disabled = not enabled or not self.account.credentials.api_key

    def update_account(self) -> None:
        """显示独立的凭据状态及存储提示，不显示任何令牌。"""
        account = self.account
        self.query_one("#account-status", Static).update(
            f"网页会话：{account.web_status}\n官方 API Key：{account.key_status}\n{account.store.warning}"
        )
        welcome = "已登录。" if account.web_status == "可用" else "尚未登录。"
        self.query_one("#welcome", Static).update(
            f"微信读书终端阅读器\n\n{welcome}\n按 ? 查看帮助，按 q 退出。"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """event 指定扫码登录或补充密钥操作。"""
        if event.button.id == "login":
            self.push_screen(LoginScreen(self.account), self.login_finished)
        elif event.button.id == "set-api-key":
            self.push_screen(ApiKeyScreen(), self.key_submitted)
        elif event.button.id == "bookshelf":
            self.push_screen(LibraryScreen(self.account), lambda _: self.update_account())

    def login_finished(self, credentials: Credentials | None) -> None:
        """扫码完成后在首页保存 credentials；取消扫描不改变当前账号。"""
        if credentials is not None:
            self.enable_account_buttons(False)
            self.query_one("#account-status", Static).update("已授权，正在验证并保存凭据…")
            self.run_worker(self.finish_login(credentials), group="account")

    async def finish_login(self, credentials: Credentials) -> None:
        """提交已确认的 credentials，保存期间不再属于可取消的扫码尝试。"""
        await self.account.accept_login(credentials)
        self.update_account()
        self.enable_account_buttons(True)

    def key_submitted(self, value: str | None) -> None:
        """value 为用户提交的密钥；取消时不修改账号。"""
        if value is not None:
            self.enable_account_buttons(False)
            self.run_worker(self.save_key(value), group="account", exclusive=True)

    async def save_key(self, value: str) -> None:
        """验证并保存 value，不将其显示在界面中。"""
        await self.account.set_api_key(value)
        self.update_account()
        self.enable_account_buttons(True)

    def action_toggle_help(self) -> None:
        """切换键位帮助的显示状态。"""
        if len(self.screen_stack) > 1:
            self.notify("Esc 返回；请按当前界面的提示操作。")
            return
        help_panel = self.query_one("#help", Static)
        help_panel.display = not help_panel.display
