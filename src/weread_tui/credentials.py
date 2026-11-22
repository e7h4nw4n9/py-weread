"""登录凭据与 macOS Keychain 存取。"""

import json
import sys
from dataclasses import asdict, dataclass

from keyring.backend import KeyringBackend
from keyring.errors import KeyringError


@dataclass(repr=False)
class Credentials:
    """账号凭据；vid 为账号标识，其余字段为网页令牌和官方密钥。"""

    vid: str = ""
    skey: str = ""
    refresh_token: str = ""
    api_key: str = ""


class CredentialStore:
    """优先使用指定 Keychain 后端，失败时仅保留当前进程内存。"""

    def __init__(self, backend: KeyringBackend | None = None) -> None:
        """backend 为系统凭据后端；None 表示仅存内存。"""
        self.backend = backend
        self.warning = "" if backend else "Keychain 不可用，凭据仅保留在本次会话。"
        self._credentials = Credentials()

    @classmethod
    def system(cls) -> "CredentialStore":
        """只启用 macOS Keychain，不选择文件存储后端。"""
        if sys.platform == "darwin":
            from keyring.backends.macOS import Keyring

            return cls(Keyring())  # type: ignore[no-untyped-call]  # 上游构造器未标注类型。
        return cls()

    def load(self) -> Credentials:
        """读取上次保存的凭据；无法读取时提示重新登录。"""
        if self.backend is not None:
            try:
                payload = self.backend.get_password("weread-tui", "credentials")
                if payload:
                    self._credentials = Credentials(**json.loads(payload))
            except (KeyringError, ValueError, TypeError):
                self.warning = "无法读取 Keychain，请重新登录；凭据仅保留在本次会话。"
                self.backend = None
        return self._credentials

    def save(self, credentials: Credentials) -> None:
        """保存 credentials；Keychain 失败不丢失当前内存中的登录。"""
        self._credentials = credentials
        if self.backend is not None:
            try:
                self.backend.set_password(
                    "weread-tui", "credentials", json.dumps(asdict(credentials))
                )
            except KeyringError:
                self.warning = "Keychain 保存失败，凭据仅保留在本次会话。"
                self.backend = None
