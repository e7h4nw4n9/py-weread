"""通过账号服务验证凭据的独立状态。"""

import httpx
import pytest
import asyncio

from weread_tui.account import AccountService
from weread_tui.credentials import Credentials, CredentialStore


@pytest.mark.parametrize("web_expired,key_expired", [(True, False), (False, True)])
async def test_credentials_are_validated_independently(web_expired: bool, key_expired: bool) -> None:
    """两个参数分别指定网页会话和 API Key 是否失效。"""
    def respond(request: httpx.Request) -> httpx.Response:
        """按 request 所用的凭据返回状态，不发出远程写操作。"""
        if request.url.host == "weread.qq.com":
            assert request.headers["X-Vid"] == "reader"
            return httpx.Response(200, json={"errCode": -2012} if web_expired else {"name": "读者"})
        assert request.headers["Authorization"] == "Bearer wrk-test"
        return httpx.Response(401 if key_expired else 200, json={"errcode": 0})

    store = CredentialStore()
    store.save(Credentials(vid="reader", skey="session", api_key="wrk-test"))
    service = AccountService(store, transport=httpx.MockTransport(respond))
    await service.restore()

    assert service.web_status == ("已失效" if web_expired else "可用")
    assert service.key_status == ("已失效" if key_expired else "可用")


async def test_cancelled_login_preparation_preserves_previous_account() -> None:
    """准备新账号时取消，不留下新凭据与旧状态的混合。"""
    waiting = asyncio.Event()

    async def respond(request: httpx.Request) -> httpx.Response:
        """阻塞 request，模拟扫码成功后仍在获取密钥。"""
        waiting.set()
        await asyncio.Future[None]()
        raise AssertionError("请求应被取消")

    store = CredentialStore()
    previous = Credentials(vid="old", skey="previous-session")
    store.save(previous)
    service = AccountService(store, transport=httpx.MockTransport(respond))
    service.credentials = previous
    task = asyncio.create_task(service.accept_login(Credentials(vid="new", skey="new-session")))
    await asyncio.wait_for(waiting.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert service.credentials == previous
    assert store.load() == previous
