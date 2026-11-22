"""账号恢复、凭据检查和登录后的保存。"""

import asyncio
from urllib.parse import quote

import httpx

from weread_tui.auth import SKILLS_PAGE, WEB_ORIGIN
from weread_tui.credentials import Credentials, CredentialStore

GATEWAY = "https://i.weread.qq.com/api/agent/gateway"
SKILL_VERSION = "1.0.6"


def create_web_client(credentials: Credentials | None = None, transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    """创建网页客户端；credentials 为空时建立独立的匿名扫码会话。"""
    headers = {"Referer": SKILLS_PAGE, "User-Agent": "Mozilla/5.0"}
    cookies = httpx.Cookies()
    if credentials is not None:
        headers.update({"X-Vid": credentials.vid, "X-Skey": credentials.skey})
        for name, value in {
            "wr_vid": credentials.vid, "wr_skey": credentials.skey,
            "wr_rt": quote(credentials.refresh_token, safe=""), "wr_ql": "0",
        }.items():
            cookies.set(name, value, domain=".weread.qq.com")
    return httpx.AsyncClient(
        base_url=WEB_ORIGIN, headers=headers, cookies=cookies, timeout=15,
        follow_redirects=True, transport=transport,
    )


def credential_status(response: httpx.Response) -> str:
    """将验证 response 转为状态，网络或业务异常不冒充凭据失效。"""
    if response.status_code in (401, 403):
        return "已失效"
    response.raise_for_status()
    data = response.json()
    code = data.get("errcode", data.get("errCode", 0))
    if code in (-2010, -2012):
        return "已失效"
    return "待验证" if code else "可用"


class AccountService:
    """分别维护网页会话与官方密钥，不将二者视为同一登录状态。"""

    def __init__(self, store: CredentialStore, transport: httpx.AsyncBaseTransport | None = None) -> None:
        """store 保存凭据，transport 可替换 HTTP 系统边界用于测试。"""
        self.store = store
        self.transport = transport
        self.credentials = Credentials()
        self.web_status = "未提供"
        self.key_status = "未提供"

    def web_client(self, credentials: Credentials | None = None) -> httpx.AsyncClient:
        """创建独立网页会话；credentials 为空时用于匿名扫码握手。"""
        return create_web_client(credentials, self.transport)

    async def restore(self) -> None:
        """读取已保存的凭据并分别验证当前有效性。"""
        self.credentials = await asyncio.to_thread(self.store.load)
        self.web_status, self.key_status = await self.validate(self.credentials)

    async def validate(self, credentials: Credentials) -> tuple[str, str]:
        """分别验证 credentials，返回两项状态而不提前修改当前账号。"""
        web_status = "未提供"
        if credentials.vid and credentials.skey:
            web_status = "待验证"
            try:
                async with self.web_client(credentials) as client:
                    response = await client.get("/api/userInfo", params={"userVid": credentials.vid})
                    web_status = credential_status(response)
            except (httpx.HTTPError, ValueError):
                pass
        key_status = "未提供"
        if credentials.api_key:
            key_status = "待验证"
            try:
                async with httpx.AsyncClient(transport=self.transport, timeout=15) as client:
                    response = await client.post(
                        GATEWAY, headers={"Authorization": f"Bearer {credentials.api_key}"},
                        json={"api_name": "/_list", "skill_version": SKILL_VERSION},
                    )
                    key_status = credential_status(response)
            except (httpx.HTTPError, ValueError):
                pass
        return web_status, key_status

    async def accept_login(self, credentials: Credentials) -> None:
        """保存扫码 credentials，并尝试读取该账号的官方密钥。"""
        try:
            async with self.web_client(credentials) as client:
                response = await client.get("/api/skills/apikeyGet", params={"only_show": "1"})
                response.raise_for_status()
                credentials.api_key = str(response.json().get("apikey") or "")
        except (httpx.HTTPError, ValueError):
            pass
        web_status, key_status = await self.validate(credentials)
        await asyncio.to_thread(self.store.save, credentials)
        self.credentials = credentials
        self.web_status, self.key_status = web_status, key_status

    async def set_api_key(self, api_key: str) -> None:
        """保存用户补充的 api_key，不替换已有网页会话。"""
        self.credentials.api_key = api_key.strip()
        await asyncio.to_thread(self.store.save, self.credentials)
        self.web_status, self.key_status = await self.validate(self.credentials)
