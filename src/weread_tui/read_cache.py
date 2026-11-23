"""账号隔离的已读内容缓存，供网络不可用时恢复本地阅读。"""

from hashlib import sha256
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


class ReadCache:
    """只存已成功读取的数据，不缓存云端位置为新的阅读记录。"""

    def __init__(self, root: Path, account_id: str) -> None:
        """root 指定缓存根目录，account_id 隔离同名书籍与章节。"""
        self.directory = root / "v1" / sha256(account_id.encode()).hexdigest()

    def path(self, key: tuple[str, ...]) -> Path:
        """把 key 转为固定缓存名，不使用外部标识作为路径。"""
        return self.directory / (sha256(json.dumps(key).encode()).hexdigest() + ".json")

    def load(self, key: tuple[str, ...]) -> dict[str, Any] | None:
        """读取 key 对应的数据，已删除或损坏的缓存按缺失处理。"""
        try:
            data = json.loads(self.path(key).read_text())
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def save(self, key: tuple[str, ...], data: dict[str, Any]) -> None:
        """将 key 的已验证 data 原子替换，避免取消或并发留下半份内容。"""
        self.directory.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False)
        temporary.replace(self.path(key))
