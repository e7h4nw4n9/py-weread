"""按账号、书籍、章节和正文版本隔离图片缓存。"""

from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile

from weread_tui.models import ChapterDocument


class ImageCache:
    """缓存文件名只来自哈希，不把归档成员名当成本地路径。"""

    def __init__(self, root: Path, document: ChapterDocument) -> None:
        """root 为缓存根目录，document 确定账号和正文版本范围。"""
        self.directory = root / "v1"
        for value in (document.book.account_id, document.book.book_id, document.chapter.uid, document.revision):
            self.directory /= sha256(value.encode()).hexdigest()

    def path(self, reference: str) -> Path:
        """返回 reference 在当前版本范围中的缓存路径。"""
        return self.directory / sha256(reference.encode()).hexdigest()

    def get(self, reference: str) -> Path | None:
        """reference 已缓存时返回路径，缺失时交由资源层获取。"""
        path = self.path(reference)
        return path if path.is_file() else None

    def save(self, reference: str, data: bytes) -> Path:
        """原子保存 reference 对应的 data，临时文件名不与并发写入冲突。"""
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.path(reference)
        with NamedTemporaryFile(dir=self.directory, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        temporary.replace(path)
        return path
