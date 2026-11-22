"""验证系统凭据边界和进程内回退。"""

from keyring.backend import KeyringBackend
from keyring.errors import KeyringError

from weread_tui.credentials import Credentials, CredentialStore


class MemoryKeyring(KeyringBackend):
    """测试用系统边界替身，不访问真实 Keychain。"""

    priority = 1

    def __init__(self) -> None:
        """初始化测试凭据空间。"""
        self.password: str | None = None

    def get_password(self, service: str, username: str) -> str | None:
        """读取 service 与 username 对应的测试凭据。"""
        return self.password

    def set_password(self, service: str, username: str, password: str) -> None:
        """保存 service、username 对应的 password。"""
        self.password = password

    def delete_password(self, service: str, username: str) -> None:
        """删除 service 与 username 对应的测试凭据。"""
        self.password = None


def test_new_store_restores_saved_credentials() -> None:
    """新实例可从系统边界恢复完整凭据，不把令牌放进 repr。"""
    backend = MemoryKeyring()
    credentials = Credentials(vid="reader", skey="secret", api_key="wrk-key")
    CredentialStore(backend).save(credentials)

    assert CredentialStore(backend).load() == credentials
    assert "secret" not in repr(credentials)
    assert "wrk-key" not in repr(credentials)


def test_keychain_failure_keeps_credentials_in_memory() -> None:
    """系统存储拒绝写入时当前账号仍可使用，并给出明确提示。"""
    class LockedKeyring(MemoryKeyring):
        """模拟系统 Keychain 写入被拒绝。"""

        def set_password(self, service: str, username: str, password: str) -> None:
            """拒绝向 service、username 写入 password。"""
            raise KeyringError("locked")

    store = CredentialStore(LockedKeyring())
    credentials = Credentials(vid="reader", skey="secret")
    store.save(credentials)

    assert store.load() == credentials
    assert "仅保留在本次会话" in store.warning
