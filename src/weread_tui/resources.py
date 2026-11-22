"""从章节元数据允许的资源包或同源图片获取插图。"""

import asyncio
import io
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import httpx
from PIL import Image

from weread_tui.auth import WEB_ORIGIN
from weread_tui.chapter_protocol import ContentError, encode_id
from weread_tui.document import ImageReference, parse_content
from weread_tui.image_cache import ImageCache
from weread_tui.models import ChapterDocument
from weread_tui.web_reader import check_response, check_service_error

MAX_RESOURCE_BYTES = 64 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
# 由微信读书图片直链实际 302 响应确认的腾讯云图片域。
WEREAD_IMAGE_HOSTS = frozenset({"wrco-40036.sh.gfp.tencent-cloud.com"})


@dataclass
class ImageResources:
    """paths 对应原始引用；missing 和 warning 明确表示图片尚未完整加载。"""

    paths: dict[str, Path] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    warning: str = ""


def resource_name(reference: str) -> str:
    """取 reference 的路径尾段，匹配微信资源包中的图片标识。"""
    return unquote(urlsplit(reference).path).rsplit("/", 1)[-1]


def archive_images(data: bytes, names: set[str]) -> dict[str, bytes]:
    """在内存读取 data 中 names 对应的普通成员，不解压路径或链接。"""
    files: dict[str, bytes] = {}
    total = 0

    def add(name: str, payload: bytes) -> None:
        """累计 name 对应的 payload，拒绝同名冲突及过大的解压总量。"""
        nonlocal total
        total += len(payload)
        if total > MAX_RESOURCE_BYTES:
            raise ContentError("章节解压后的图片资源过大。")
        if name in files and files[name] != payload:
            raise ContentError("章节图片存在同名冲突，未选择不确定的图片。")
        files[name] = payload

    if zipfile.is_zipfile(io.BytesIO(data)):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for member in archive.infolist():
                name = resource_name(member.filename)
                if not member.is_dir() and name in names:
                    with archive.open(member) as stream:
                        add(name, stream.read(MAX_RESOURCE_BYTES + 1))
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar_archive:
            for tar_member in tar_archive:
                name = resource_name(tar_member.name)
                if tar_member.isfile() and name in names:
                    tar_stream = tar_archive.extractfile(tar_member)
                    if tar_stream is not None:
                        with tar_stream:
                            add(name, tar_stream.read(MAX_RESOURCE_BYTES + 1))
    return files


def verify_image(data: bytes) -> None:
    """依据 data 的实际内容校验图片，限制单图内存需求。"""
    if len(data) > MAX_RESOURCE_BYTES:
        raise ContentError("图片资源过大，未加载该图。")
    with Image.open(io.BytesIO(data)) as image:
        if image.width * image.height > MAX_IMAGE_PIXELS:
            raise ContentError("图片像素尺寸过大，未加载该图。")
        image.load()


async def download_image_resource(client: httpx.AsyncClient, url: str, allowed_hosts: set[str], referer: str) -> bytes:
    """读取 url，逐跳确认重定向仍在 allowed_hosts 或微信读书 HTTPS 域内。"""
    for _ in range(6):
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or not (host in allowed_hosts or host in WEREAD_IMAGE_HOSTS
                                            or host.endswith(".weread.qq.com") or host == "weread.qq.com"):
            raise ContentError(f"图片地址不在当前章节允许范围（{parsed.scheme or '相对引用'}://{host}）。")
        async with client.stream("GET", url, headers={"Referer": referer}) as response:
            if response.is_redirect and "Location" in response.headers:
                url = urljoin(url, response.headers["Location"])
                continue
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > MAX_RESOURCE_BYTES:
                    raise ContentError("章节资源包过大，未继续下载。")
            return bytes(data)
    raise ContentError("图片重定向次数过多，未继续下载。")


async def load_images(client: httpx.AsyncClient, document: ChapterDocument, cache_root: Path,
                      transport: httpx.AsyncBaseTransport | None = None) -> ImageResources:
    """用 client 查询 document 的资源位置，cache_root 保存已验证图片，transport 替换下载边界。"""
    references = list(dict.fromkeys(block.src for block in parse_content(document) if isinstance(block, ImageReference)))
    result = ImageResources()
    cache = ImageCache(cache_root, document)
    for reference in references:
        path = cache.get(reference)
        if path is not None:
            result.paths[reference] = path
    missing = [reference for reference in references if reference not in result.paths]
    if not missing:
        return result
    referer = f"{WEB_ORIGIN}/web/reader/{encode_id(document.book.book_id)}k{encode_id(document.chapter.uid)}"
    try:
        response = await client.post("/web/book/chapterInfos", json={"bookIds": [document.book.book_id], "synckeys": [0]})
        check_response(response)
        records = response.json()
        if isinstance(records, dict):
            check_service_error(records)
            records = records.get("data", [records])
        if isinstance(records, dict):
            records = [records]
        tar_url = ""
        for record in records:
            if str(record["bookId"]) == document.book.book_id:
                chapters = record.get("updated", record.get("chapterInfos", record.get("chapters", [])))
                tar_url = next((chapter.get("tar", "") for chapter in chapters
                                if str(chapter["chapterUid"]) == document.chapter.uid), "")
        allowed_hosts = {urlsplit(tar_url).hostname} if tar_url else set()
        hosts = {host for host in allowed_hosts if host}
        async with httpx.AsyncClient(transport=transport, timeout=20, cookies=client.cookies) as downloader:
            packed: dict[str, bytes] = {}
            if tar_url:
                try:
                    data = await download_image_resource(downloader, tar_url, hosts, referer)
                    packed = await asyncio.to_thread(archive_images, data, {resource_name(ref) for ref in missing})
                except (httpx.HTTPError, OSError, ValueError, tarfile.TarError, zipfile.BadZipFile):
                    result.warning = "资源包不可用，已尝试正文中的允许范围图片地址。"
            for reference in missing:
                image_data = None
                try:
                    image_data = packed.get(resource_name(reference))
                    if image_data is None:
                        image_data = await download_image_resource(downloader, reference, hosts, WEB_ORIGIN + "/")
                    await asyncio.to_thread(verify_image, image_data)
                    result.paths[reference] = await asyncio.to_thread(cache.save, reference, image_data)
                except (httpx.HTTPError, OSError, ValueError, Image.DecompressionBombError) as error:
                    if isinstance(error, ContentError):
                        result.warning = str(error)
                    elif isinstance(error, httpx.HTTPStatusError):
                        result.warning = f"图片下载被拒绝（HTTP {error.response.status_code}）。"
                    else:
                        result.warning = f"图片获取或解码失败（{type(error).__name__}）。"
    except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError, tarfile.TarError, zipfile.BadZipFile):
        result.warning = "章节资源暂时不可用，未将图片缺失视为完整加载成功。"
    result.missing = [reference for reference in references if reference not in result.paths]
    return result
