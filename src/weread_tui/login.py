"""扫码及官方密钥输入界面。"""

import asyncio

import httpx
import qrcode  # type: ignore[import-untyped]  # 上游尚未提供类型标记。
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from weread_tui.account import AccountService
from weread_tui.auth import LoginAttempt, LoginError
from weread_tui.credentials import Credentials


def qr_text(url: str) -> str:
    """把 url 编为半高字符二维码，保留四格白色静区。"""
    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, border=4)
    code.add_data(url)
    code.make(fit=True)
    matrix = code.get_matrix()
    if len(matrix) % 2:
        matrix.append([False] * len(matrix))
    return "\n".join(
        "".join(" ▄▀█"[2 * top + bottom] for top, bottom in zip(matrix[row], matrix[row + 1]))
        for row in range(0, len(matrix), 2)
    )


class LoginScreen(ModalScreen[Credentials | None]):
    """显示二维码并管理本次登录的轮询生命周期。"""

    BINDINGS = [Binding("escape", "cancel", "取消", priority=True)]
    DEFAULT_CSS = """
    LoginScreen { align: center middle; }
    LoginScreen Static { width: auto; height: auto; }
    LoginScreen #qr { color: black; background: white; }
    LoginScreen Input { width: 30; display: none; }
    LoginScreen Button { margin: 0 1; }
    """

    def __init__(self, account: AccountService) -> None:
        """account 接收本次成功登录的账号。"""
        super().__init__()
        self.account = account
        self._otp: asyncio.Future[str] | None = None

    def compose(self) -> ComposeResult:
        """生成二维码、状态和验证码输入。"""
        yield Static("请用微信扫描二维码", id="login-title")
        yield Static("", id="qr", markup=False)
        yield Static("正在获取二维码…", id="login-message", markup=False)
        yield Input(placeholder="手机上的四位验证码", id="otp", max_length=4)
        yield Button("重新扫码", id="retry")
        yield Static("Esc：取消；二维码显示不全时请扩大终端窗口。")

    def on_mount(self) -> None:
        """进入界面后开始一次扫码尝试。"""
        self.start_login()

    def start_login(self) -> None:
        """重试前取消旧轮询，只保留当前扫码尝试。"""
        self.run_worker(self.login(), group="qr", exclusive=True)

    async def login(self) -> None:
        """完成匿名扫码握手，退出界面时由 Textual 取消此任务。"""
        message = self.query_one("#login-message", Static)
        qr = self.query_one("#qr", Static)
        otp_input = self.query_one("#otp", Input)
        qr.update("")
        qr.display = True
        otp_input.display = False
        message.update("正在获取二维码…")
        try:
            async with self.account.web_client() as client:
                attempt = LoginAttempt(client)
                qr.update(qr_text(await attempt.start()))
                message.update("等待扫码确认")
                otp = ""
                while True:
                    result = await attempt.poll(otp)
                    message.update(result.message)
                    if result.credentials is not None:
                        self.dismiss(result.credentials)
                        return
                    if result.needs_otp:
                        qr.display = False
                        otp_input.display = True
                        otp_input.value = ""
                        otp_input.focus()
                        self._otp = asyncio.get_running_loop().create_future()
                        # 手机验证码输入也受本次二维码的单调时钟期限约束。
                        async with asyncio.timeout_at(attempt.deadline):
                            otp = await self._otp
                        self._otp = None
                        otp_input.display = False
                    else:
                        await asyncio.sleep(1.5)
        except (LoginError, TimeoutError) as error:
            qr.update("")
            message.update(str(error) or "二维码已过期，请重新扫码。")
        except httpx.HTTPError:
            message.update("无法连接登录服务，请检查网络后重新扫码。")
        except (ValueError, KeyError):
            message.update("登录服务响应格式已变化，请稍后重试。")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """event 携带用户提交的手机验证码。"""
        value = event.value.strip()
        if len(value) != 4 or not value.isascii() or not value.isdecimal():
            self.query_one("#login-message", Static).update("请输入四位数字验证码。")
        elif self._otp is not None and not self._otp.done():
            self._otp.set_result(value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """event 为重新扫码按钮操作。"""
        self.start_login()

    def action_cancel(self) -> None:
        """关闭扫码界面，同时取消绑定在本界面上的后台任务。"""
        self.dismiss(None)


class ApiKeyScreen(ModalScreen[str | None]):
    """以隐藏字符输入官方 API Key。"""

    BINDINGS = [Binding("escape", "cancel", "取消", priority=True)]
    DEFAULT_CSS = """
    ApiKeyScreen { align: center middle; }
    ApiKeyScreen Static { width: auto; height: auto; }
    ApiKeyScreen Input { width: 60; max-width: 90%; }
    """

    def compose(self) -> ComposeResult:
        """生成隐藏输入框，不回显已保存的密钥。"""
        yield Static("输入官方 API Key，Enter 保存，Esc 取消。")
        yield Input(placeholder="wrk-…", password=True, id="api-key")
        yield Static("", id="key-message")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """event 提供待保存的官方密钥。"""
        value = event.value.strip()
        if not value.startswith("wrk-") or len(value) <= 4:
            self.query_one("#key-message", Static).update("请输入以 wrk- 开头的完整 API Key。")
            return
        event.input.value = ""
        self.dismiss(value)

    def action_cancel(self) -> None:
        """取消输入，不修改已有密钥。"""
        self.query_one(Input).value = ""
        self.dismiss(None)
