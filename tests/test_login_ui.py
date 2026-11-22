"""在界面边界验证取消扫码。"""

import asyncio

import httpx

from weread_tui.account import AccountService
from weread_tui.app import WereadApp
from weread_tui.credentials import CredentialStore
from textual.widgets import Input, Static


async def test_cancel_stops_pending_login_request() -> None:
    """取消登录时停止长轮询，保留首页可继续操作。"""
    polling = asyncio.Event()
    cancelled = asyncio.Event()

    async def respond(request: httpx.Request) -> httpx.Response:
        """根据 request 模拟等待中的扫码服务。"""
        if request.url.path == "/r/weread-skills":
            return httpx.Response(200)
        if request.url.path.endswith("getLoginUid"):
            return httpx.Response(200, json={"uid": "attempt"})
        polling.set()
        try:
            await asyncio.Future[None]()
        finally:
            cancelled.set()
        raise AssertionError("长轮询应被取消")

    account = AccountService(CredentialStore(), transport=httpx.MockTransport(respond))
    app = WereadApp(account=account)
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        await pilot.click("#login")
        await asyncio.wait_for(polling.wait(), timeout=2)
        await pilot.press("escape")
        await asyncio.wait_for(cancelled.wait(), timeout=2)
        assert app.query_one("#login").display


async def test_user_can_supply_api_key_without_exposing_it() -> None:
    """通过隐藏输入提交官方密钥，首页仅显示验证状态。"""
    def respond(request: httpx.Request) -> httpx.Response:
        """验证 request 使用了新密钥，然后返回可用状态。"""
        assert request.headers["Authorization"] == "Bearer wrk-user-key"
        return httpx.Response(200, json={"errcode": 0})

    account = AccountService(CredentialStore(), transport=httpx.MockTransport(respond))
    app = WereadApp(account=account)
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        await pilot.click("#set-api-key")
        field = app.screen.query_one("#api-key", Input)
        assert field.password
        await pilot.click("#api-key")
        await pilot.press(*"wrk-user-key", "enter")
        await pilot.pause()
        status = str(app.query_one("#account-status", Static).content)
        assert "官方 API Key：可用" in status
        assert "网页会话：未提供" in status
        assert "wrk-user-key" not in status


async def test_successful_scan_updates_homepage_and_preserves_web_session() -> None:
    """扫码成功后即使没有官方密钥，网页会话仍可用。"""
    def respond(request: httpx.Request) -> httpx.Response:
        """为 request 返回独立编造的协议样本，不使用真实凭据。"""
        path = request.url.path
        if path == "/r/weread-skills":
            return httpx.Response(200)
        if path.endswith("getLoginUid"):
            return httpx.Response(200, json={"uid": "attempt"})
        if path.endswith("getLoginInfo"):
            return httpx.Response(200, json={"webLoginVid": "reader", "accessToken": "secret"})
        if path.endswith("apikeyGet"):
            return httpx.Response(200, json={})
        assert path == "/api/userInfo"
        return httpx.Response(200, json={"name": "读者"})

    account = AccountService(CredentialStore(), transport=httpx.MockTransport(respond))
    app = WereadApp(account=account)
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        await pilot.click("#login")
        await pilot.pause()
        status = str(app.query_one("#account-status", Static).content)
        assert "网页会话：可用" in status
        assert "官方 API Key：未提供" in status
        assert "secret" not in status


async def test_expired_qr_can_be_replaced_in_place() -> None:
    """扫码过期后可在原界面获取新的二维码，不沿用旧 UID。"""
    from textual.widgets import Button

    attempts = 0
    expired = asyncio.Event()

    def respond(request: httpx.Request) -> httpx.Response:
        """按 request 为每次扫码生成不同 UID 并返回过期状态。"""
        nonlocal attempts
        if request.url.path == "/r/weread-skills":
            return httpx.Response(200)
        if request.url.path.endswith("getLoginUid"):
            attempts += 1
            return httpx.Response(200, json={"uid": f"attempt-{attempts}"})
        expired.set()
        return httpx.Response(200, json={"logicCode": "LOGIN_TIMEOUT"})

    account = AccountService(CredentialStore(), transport=httpx.MockTransport(respond))
    app = WereadApp(account=account)
    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        await pilot.click("#login")
        await asyncio.wait_for(expired.wait(), timeout=2)
        await pilot.pause()
        assert "过期" in str(app.screen.query_one("#login-message", Static).content)
        await pilot.click(app.screen.query_one("#retry", Button))
        await pilot.pause()
        assert attempts == 2
