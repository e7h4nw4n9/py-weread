"""通过界面交互验证启动、帮助与退出。"""

from textual.widgets import Static

from weread_tui.app import WereadApp
from weread_tui.account import AccountService
from weread_tui.credentials import CredentialStore


async def test_user_can_open_help_and_quit() -> None:
    """用户可从首页查看帮助，再正常退出应用。"""
    app = WereadApp(account=AccountService(CredentialStore()))
    async with app.run_test() as pilot:
        assert "尚未登录" in str(app.query_one("#welcome", Static).content)
        assert not app.query_one("#help", Static).display

        await pilot.press("?")
        assert app.query_one("#help", Static).display
        assert "退出应用" in str(app.query_one("#help", Static).content)

        await pilot.press("?")
        assert not app.query_one("#help", Static).display

        await pilot.press("q")
        await pilot.pause()
        assert not app.is_running
