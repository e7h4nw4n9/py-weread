"""仅由子进程加载的 XHTML/CSS 分页实现。"""

from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin

from PIL import Image
from weasyprint import CSS, HTML  # type: ignore[import-untyped]
from weasyprint.urls import URLFetcher, URLFetcherResponse  # type: ignore[import-untyped]

BASE_URL = "https://weread.invalid/"


def resource_url(reference: str) -> str:
    """把 reference 规范为排版引擎使用的绝对 URI，不发起网络请求。"""
    return quote(urljoin(BASE_URL, reference), safe="/:?#[]@!$&'()*+,;=~%")


class CachedResources(URLFetcher):  # type: ignore[misc]  # WeasyPrint 尚未标注类型。
    """排版只能读取应用已获取的图片，不加载文档指定的任意网络或文件。"""

    def __init__(self, images: dict[str, str]) -> None:
        """images 将原始图片引用关联到已经验证的本地缓存。"""
        super().__init__(allowed_protocols=())
        self.images = {resource_url(reference): Path(path) for reference, path in images.items()}

    def fetch(self, url: str, headers: dict[str, str] | None = None) -> URLFetcherResponse:
        """读取 url 的已验证图片；headers 不参与本地资源访问。"""
        path = self.images.get(resource_url(url))
        if path is None:
            raise ValueError("原书资源未在本章缓存中，不执行额外文件或网络访问。")
        data = path.read_bytes()
        with Image.open(path) as image:
            mime = Image.MIME.get(image.format or "", "application/octet-stream")
        return URLFetcherResponse(url, body=data, headers={"Content-Type": mime})


def paginate(job: dict[str, Any]) -> int:
    """按 job 中的源文档、缓存资源和像素视口生成分页 PDF。"""
    width, height = int(job["width"]), int(job["height"])
    if width < 160 or height < 160:
        raise ValueError("阅读区域太小，请扩大终端窗口后重试。")
    fetcher = CachedResources(job["images"])
    css = f"""
    @page {{ size: {width}px {height}px !important; margin: 24px 32px !important;
             background: #f5ead7; }}
    body {{ font-family: 'Songti SC', serif !important; font-size: 20px !important;
            line-height: 1.6 !important; color: #302a24; }}
    img {{ max-width: 100%; max-height: {height - 48}px; height: auto; }}
    pre {{ white-space: pre-wrap; overflow-wrap: anywhere; }}
    """
    document = HTML(string=job["source"], base_url=BASE_URL, url_fetcher=fetcher).render(
        stylesheets=[CSS(string=job["stylesheet"], base_url=BASE_URL, url_fetcher=fetcher), CSS(string=css)],
        presentational_hints=True,
    )
    path = Path(job["directory"]) / "chapter.pdf"
    temporary = path.with_suffix(".tmp")
    document.write_pdf(temporary)
    temporary.replace(path)
    return len(document.pages)
