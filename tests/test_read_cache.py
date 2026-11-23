"""验证断网时只恢复本人已经读取并缓存的内容。"""

import json
from pathlib import Path

import httpx
import pytest

from weread_tui.credentials import Credentials
from weread_tui.provider import ProviderError, WereadProvider
from weread_tui.chapter_protocol import ContentError
from weread_tui.cloud_progress import CloudProgress
from weread_tui.positions import ContentAnchor
from weread_tui.progress import ReadingProgress
from weread_tui.reading_resume import ResumePlan, load_resume


async def test_offline_book_catalogue_and_chapter_use_account_cache(tmp_path: Path) -> None:
    """tmp_path 隔离缓存；断网重建 Provider 后仍能读取先前已校验的正文。"""
    fixture = json.loads((Path(__file__).parent / "fixtures/chapter.json").read_text())

    def online(request: httpx.Request) -> httpx.Response:
        """request 仅获得自造书架、目录、正文与空样式。"""
        if request.url.path.startswith("/web/reader/"):
            state = {"reader": {"psvts": "state", "bookInfo": {"bookId": "1"}, "currentChapter": {"chapterUid": 2}}}
            return httpx.Response(200, text=f"<script>window.__INITIAL_STATE__={json.dumps(state)};</script>")
        if request.url.path.startswith("/web/book/chapter/"):
            endpoint = request.url.path.rsplit("/", 1)[1]
            if endpoint == "e_2":
                return httpx.Response(200, json={})
            return httpx.Response(200, text=fixture["parts"][("e_0", "e_1", "e_3").index(endpoint)])
        api = json.loads(request.content)["api_name"]
        if api == "/shelf/sync":
            return httpx.Response(200, json={"books": [{"bookId": "1", "title": "缓存书"}]})
        assert api == "/book/chapterinfo"
        return httpx.Response(200, json={"chapters": [{"chapterUid": 2, "title": "缓存章"}]})

    def offline(request: httpx.Request) -> httpx.Response:
        """request 代表真实网络断开，不允许依赖线上任何接口。"""
        raise httpx.ConnectError("offline", request=request)

    credentials = Credentials(vid="reader", skey="session", api_key="wrk-test")
    root = tmp_path / "resources"
    provider = WereadProvider(credentials, httpx.MockTransport(online), root, cache_reads=True)
    book = (await provider.get_bookshelf())[0]
    chapter = (await provider.get_chapters(book))[0]
    document = await provider.get_chapter(book, chapter)
    await provider.get_stylesheet(document)
    disconnected = WereadProvider(credentials, httpx.MockTransport(offline), root, cache_reads=True)
    assert await disconnected.get_bookshelf() == [book]
    assert await disconnected.get_chapters(book) == [chapter]
    assert await disconnected.get_chapter(book, chapter) == document
    assert await disconnected.get_stylesheet(document) == ""
    assert disconnected.using_cache
    local = ReadingProgress("reader", "1", "2", ContentAnchor("t0", 0, document.revision), 100)
    plan = ResumePlan(CloudProgress("reader", "1", "2", 80, 150), local, "采用云端阅读位置。")
    _, point, _ = await load_resume(disconnected, book, [chapter], plan)
    assert point.source == "local"
    assert "缓存正文" in point.notice
    other = WereadProvider(Credentials(vid="other", skey="session", api_key="wrk-other"),
                           httpx.MockTransport(offline), root, cache_reads=True)
    with pytest.raises(ProviderError):
        await other.get_bookshelf()
    for status in (401, 403):
        denied = WereadProvider(credentials, httpx.MockTransport(lambda request: httpx.Response(status)), root, cache_reads=True)
        with pytest.raises(ContentError):
            await denied.get_chapter(book, chapter)
        with pytest.raises(ProviderError):
            await denied.get_bookshelf()
        assert not denied.using_cache
