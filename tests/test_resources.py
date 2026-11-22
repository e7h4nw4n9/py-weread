"""验证资源包、账号隔离缓存和不访问本地文件的资源解析。"""

import io
import zipfile
import tarfile
from pathlib import Path

import httpx
from PIL import Image
import pytest

from weread_tui.account import create_web_client
from weread_tui.credentials import Credentials
from weread_tui.models import Book, Chapter, ChapterDocument
from weread_tui.resources import load_images, verify_image


async def test_zip_images_are_cached_by_account_without_extracting_paths(tmp_path: Path) -> None:
    """tmp_path 为隔离缓存；归档路径不会作为本地路径解压。"""
    image = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(image, format="PNG")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../../picture-key", image.getvalue())
    downloads = 0

    def respond(request: httpx.Request) -> httpx.Response:
        """为 request 提供章节级资源地址及 ZIP 二进制。"""
        nonlocal downloads
        if request.url.path == "/web/book/chapterInfos":
            return httpx.Response(200, json=[{"bookId": "1", "updated": [
                {"chapterUid": 2, "tar": "https://res.weread.qq.com/chapter-assets"},
            ]}])
        assert request.url.host == "res.weread.qq.com"
        assert "x-skey" not in request.headers
        downloads += 1
        return httpx.Response(200, content=archive.getvalue())

    document = ChapterDocument(Book("reader", "1", "书"), Chapter("2", "章"),
                               '<p>正文</p><img src="Images/picture-key">', "epub")
    async with create_web_client(Credentials(vid="reader", skey="secret"), httpx.MockTransport(respond)) as client:
        resources = await load_images(client, document, tmp_path, transport=httpx.MockTransport(respond))
        cached = await load_images(client, document, tmp_path, transport=httpx.MockTransport(respond))
    assert not resources.missing
    assert resources.paths["Images/picture-key"].is_relative_to(tmp_path)
    assert cached.paths == resources.paths
    assert downloads == 1
    assert not (tmp_path.parent / "picture-key").exists()


async def test_missing_images_do_not_trigger_local_or_arbitrary_network_access(tmp_path: Path) -> None:
    """tmp_path 是隔离缓存；正文不能指定任意主机或本地文件读取。"""
    def respond(request: httpx.Request) -> httpx.Response:
        """只允许 request 查询官方资源目录，其余访问使测试失败。"""
        assert request.url.path == "/web/book/chapterInfos"
        return httpx.Response(200, json=[{"bookId": "1", "updated": []}])

    document = ChapterDocument(Book("reader", "1", "书"), Chapter("2", "章"),
        '<img src="file:///etc/passwd"><img src="https://example.invalid/private">', "epub")
    transport = httpx.MockTransport(respond)
    async with create_web_client(Credentials(vid="reader", skey="secret"), transport) as client:
        resources = await load_images(client, document, tmp_path, transport)
    assert len(resources.missing) == 2
    assert not resources.paths
    assert resources.warning


async def test_tar_picture_cache_is_not_shared_between_accounts(tmp_path: Path) -> None:
    """tmp_path 相同也不能让两个账号共用图片命名空间。"""
    image = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(image, format="PNG")
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as bundle:
        member = tarfile.TarInfo("Images/picture-key")
        member.size = len(image.getvalue())
        bundle.addfile(member, io.BytesIO(image.getvalue()))

    def respond(request: httpx.Request) -> httpx.Response:
        """按 request 返回相同图包，缓存仍须保持账号隔离。"""
        if request.url.path == "/web/book/chapterInfos":
            return httpx.Response(200, json=[{"bookId": "1", "updated": [
                {"chapterUid": 2, "tar": "https://res.weread.qq.com/assets"},
            ]}])
        return httpx.Response(200, content=archive.getvalue())

    paths = []
    transport = httpx.MockTransport(respond)
    for account_id in ("first", "second"):
        document = ChapterDocument(Book(account_id, "1", "书"), Chapter("2", "章"), '<img src="picture-key">', "epub")
        async with create_web_client(Credentials(vid=account_id, skey="secret"), transport) as client:
            resources = await load_images(client, document, tmp_path, transport)
        paths.append(resources.paths["picture-key"])
    assert paths[0] != paths[1]


def test_truncated_jpeg_cannot_enter_image_cache() -> None:
    """仅头部可读但像素被截断的 JPEG 必须在缓存前失败。"""
    picture = io.BytesIO()
    Image.new("RGB", (100, 100), "red").save(picture, format="JPEG")
    with pytest.raises(OSError):
        verify_image(picture.getvalue()[:-100])


@pytest.mark.parametrize("target", [
    "https://res.weread.qq.com/current-name.png",
    "https://wrco-40036.sh.gfp.tencent-cloud.com/current-name.png",
])
async def test_image_url_redirect_is_followed_without_auth_headers(tmp_path: Path, target: str) -> None:
    """tmp_path 隔离缓存，target 覆盖同源及实际观察到的腾讯云重定向。"""
    picture = io.BytesIO()
    Image.new("RGB", (8, 8), "green").save(picture, format="PNG")
    reference = "https://res.weread.qq.com/old-name.png"
    requested: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        """元数据无包，原始 request 重定向，新地址返回普通图片。"""
        if request.url.path == "/web/book/chapterInfos":
            return httpx.Response(200, json=[{"bookId": "1", "updated": []}])
        requested.append(request.url.path)
        assert "x-skey" not in request.headers
        assert "authorization" not in request.headers
        if request.url.path == "/old-name.png":
            return httpx.Response(302, headers={"Location": target})
        assert request.url.path == "/current-name.png"
        if request.url.host == "wrco-40036.sh.gfp.tencent-cloud.com":
            assert "cookie" not in request.headers
        return httpx.Response(200, content=picture.getvalue())

    transport = httpx.MockTransport(respond)
    document = ChapterDocument(Book("reader", "1", "书"), Chapter("2", "章"), f'<img src="{reference}">', "epub")
    async with create_web_client(Credentials(vid="reader", skey="secret"), transport) as client:
        resources = await load_images(client, document, tmp_path, transport)
    assert not resources.missing
    assert resources.paths[reference].read_bytes() == picture.getvalue()
    assert requested == ["/old-name.png", "/current-name.png"]


async def test_image_redirect_cannot_request_an_unapproved_host(tmp_path: Path) -> None:
    """tmp_path 为隔离缓存；重定向不能扩大正文的资源访问范围。"""
    def respond(request: httpx.Request) -> httpx.Response:
        """原图 request 跳到非允许域，目标请求不应真正发生。"""
        assert request.url.host != "example.invalid"
        if request.url.path == "/web/book/chapterInfos":
            return httpx.Response(200, json=[{"bookId": "1", "updated": []}])
        return httpx.Response(302, headers={"Location": "https://example.invalid/private.png"})

    transport = httpx.MockTransport(respond)
    document = ChapterDocument(Book("reader", "1", "书"), Chapter("2", "章"),
                               '<img src="https://res.weread.qq.com/redirect.png">', "epub")
    async with create_web_client(Credentials(vid="reader", skey="secret"), transport) as client:
        resources = await load_images(client, document, tmp_path, transport)
    assert resources.missing
    assert "允许" in resources.warning
