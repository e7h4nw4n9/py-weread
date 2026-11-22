"""在 HTTP 边界验证扫码协议。"""

import httpx
import pytest
import asyncio

from weread_tui.auth import LoginAttempt, LoginError


async def test_scan_returns_web_credentials() -> None:
    """扫码成功后从响应正文获取凭据，不依赖 Set-Cookie。"""
    def respond(request: httpx.Request) -> httpx.Response:
        """返回本测试所需的公开接口响应；request 为实际请求。"""
        if request.url.path == "/r/weread-skills":
            return httpx.Response(200)
        if request.url.path == "/api/auth/getLoginUid":
            return httpx.Response(200, json={"uid": "login-123"})
        assert request.url.params["uid"] == "login-123"
        return httpx.Response(200, json={
            "webLoginVid": "reader-123", "accessToken": "session-secret",
            "refreshToken": "refresh-secret",
        })

    async with httpx.AsyncClient(
        base_url="https://weread.qq.com", transport=httpx.MockTransport(respond)
    ) as client:
        login = LoginAttempt(client)
        assert await login.start() == "https://weread.qq.com/web/confirm?uid=login-123"
        result = await login.poll()

    assert result.credentials is not None
    assert result.credentials.vid == "reader-123"
    assert result.credentials.skey == "session-secret"


@pytest.mark.parametrize("code", ["NEED_OTP", "OTP_NOT_MATCH"])
async def test_phone_code_can_be_submitted_again(code: str) -> None:
    """code 为需输入或重输验证码的服务状态。"""
    def respond(request: httpx.Request) -> httpx.Response:
        """按 request 返回握手或验证码要求。"""
        if request.url.path == "/r/weread-skills":
            return httpx.Response(200)
        if request.url.path.endswith("getLoginUid"):
            return httpx.Response(200, json={"uid": "attempt"})
        assert request.url.params["otp"] == "1234"
        return httpx.Response(200, json={"logicCode": code})

    async with httpx.AsyncClient(base_url="https://weread.qq.com", transport=httpx.MockTransport(respond)) as client:
        login = LoginAttempt(client)
        await login.start()
        assert (await login.poll("1234")).needs_otp


@pytest.mark.parametrize("payload", [{"logicCode": "LOGIN_TIMEOUT"}, {"errCode": -2013}])
async def test_expired_qr_requires_a_new_attempt(payload: dict[str, object]) -> None:
    """payload 为二维码失效响应，不能继续等待成功。"""
    def respond(request: httpx.Request) -> httpx.Response:
        """按 request 返回握手或过期响应。"""
        if request.url.path == "/r/weread-skills":
            return httpx.Response(200)
        if request.url.path.endswith("getLoginUid"):
            return httpx.Response(200, json={"uid": "attempt"})
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(base_url="https://weread.qq.com", transport=httpx.MockTransport(respond)) as client:
        login = LoginAttempt(client)
        await login.start()
        with pytest.raises(LoginError, match="过期"):
            await login.poll()


async def test_poll_cannot_accept_credentials_after_total_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """即使请求未达到各网络阶段超时，也不能跨越扫码总期限。"""
    async def respond(request: httpx.Request) -> httpx.Response:
        """对 request 返回延迟到截止时间之后的授权响应。"""
        if request.url.path == "/r/weread-skills":
            return httpx.Response(200)
        if request.url.path.endswith("getLoginUid"):
            return httpx.Response(200, json={"uid": "attempt"})
        await asyncio.sleep(0.1)
        return httpx.Response(200, json={"webLoginVid": "reader", "accessToken": "secret"})

    monkeypatch.setattr("weread_tui.auth.monotonic", lambda: 100.0)
    async with httpx.AsyncClient(base_url="https://weread.qq.com", transport=httpx.MockTransport(respond)) as client:
        login = LoginAttempt(client)
        await login.start()
        monkeypatch.setattr("weread_tui.auth.monotonic", lambda: 399.99)
        with pytest.raises(LoginError, match="过期"):
            await login.poll()
