"""扫码握手，只从已授权响应中获取网页凭据。"""

import asyncio
from dataclasses import dataclass
from time import monotonic
from urllib.parse import urlencode

import httpx

from weread_tui.credentials import Credentials

WEB_ORIGIN = "https://weread.qq.com"
SKILLS_PAGE = f"{WEB_ORIGIN}/r/weread-skills"


class LoginError(Exception):
    """可直接显示给用户的登录失败原因。"""


@dataclass
class LoginResult:
    """一次轮询的结果；credentials 成功时提供，needs_otp 表示需手机验证码。"""

    credentials: Credentials | None = None
    needs_otp: bool = False
    message: str = "等待扫码"


class LoginAttempt:
    """单次扫码尝试，与已有账号使用不同的 HTTP 会话。"""

    def __init__(self, client: httpx.AsyncClient) -> None:
        """client 为本次扫码专用的客户端，由调用者负责关闭。"""
        self.client = client
        self.uid = ""
        self.deadline = 0.0

    async def start(self) -> str:
        """初始化匿名会话并返回二维码内容链接。"""
        response = await self.client.get("/r/weread-skills")
        response.raise_for_status()
        response = await self.client.get("/api/auth/getLoginUid")
        response.raise_for_status()
        self.uid = str(response.json()["uid"])
        if not self.uid:
            raise LoginError("未获取到登录二维码，请重试。")
        self.deadline = monotonic() + 300
        return f"{WEB_ORIGIN}/web/confirm?{urlencode({'uid': self.uid})}"

    async def poll(self, otp: str = "") -> LoginResult:
        """轮询授权结果；otp 为手机显示的四位验证码。"""
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise LoginError("二维码已过期，请重新扫码。")
        try:
            async with asyncio.timeout(remaining):
                response = await self.client.get(
                    "/api/auth/getLoginInfo", params={"uid": self.uid, "otp": otp},
                    timeout=min(60, remaining),
                )
        except TimeoutError:
            raise LoginError("二维码已过期，请重新扫码。") from None
        except httpx.ReadTimeout:
            return LoginResult()
        if response.status_code == 401:
            raise LoginError("二维码已过期，请重新扫码。")
        response.raise_for_status()
        data = response.json()
        if data.get("webLoginVid") and data.get("accessToken"):
            return LoginResult(credentials=Credentials(
                vid=str(data["webLoginVid"]), skey=str(data["accessToken"]),
                refresh_token=str(data.get("refreshToken", "")),
            ))
        code = data.get("logicCode", "")
        if code in ("NEED_OTP", "OTP_NOT_MATCH"):
            message = "请输入手机上的四位验证码" if code == "NEED_OTP" else "验证码不正确，请重试"
            return LoginResult(needs_otp=True, message=message)
        if code in ("OTP_EXPIRED", "LOGIN_TIMEOUT") or data.get("errCode", data.get("errcode")) in (-2012, -2013):
            raise LoginError("二维码或验证码已过期，请重新扫码。")
        if code or data.get("errCode", data.get("errcode", 0)):
            raise LoginError("登录服务拒绝了本次请求，请重新扫码。")
        return LoginResult()
