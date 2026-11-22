"""验证官方网关书架与目录的外部契约。"""

import json

import httpx
import pytest

from weread_tui.credentials import Credentials
from weread_tui.provider import Book, ProviderError, WereadProvider


async def test_bookshelf_to_catalogue_preserves_account_and_chapter_uid() -> None:
    """目录采用 chapterUid 而非序号，升级通知不阻断正常数据。"""
    requests: list[dict[str, object]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        """按 request 返回自造的官方接口响应。"""
        assert request.headers["Authorization"] == "Bearer wrk-reader"
        data = json.loads(request.content)
        requests.append(data)
        if data["api_name"] == "/shelf/sync":
            return httpx.Response(200, json={
                "books": [{"bookId": "book-1", "title": "中文书", "author": "作者"}],
                "albums": [{"albumInfo": {"albumId": "audio-1", "name": "有声书"}}],
                "mp": {"name": "文章收藏"}, "upgrade_info": {"latest_version": "1.0.7"},
            })
        return httpx.Response(200, json={"chapters": [
            {"chapterUid": 81, "chapterIdx": 1, "title": "第一部分", "level": 1},
            {"chapterUid": 92, "chapterIdx": 2, "title": "第一章", "level": 2},
        ]})

    provider = WereadProvider(Credentials(vid="reader", api_key="wrk-reader"), httpx.MockTransport(respond))
    books = await provider.get_bookshelf()
    assert len(books) == 3
    assert books[0].account_id == "reader"
    assert books[1].kind == "audio"
    assert books[2].kind == "articles"
    assert provider.upgrade_available
    chapters = await provider.get_chapters(books[0])
    assert [(chapter.uid, chapter.level) for chapter in chapters] == [("81", 1), ("92", 2)]
    assert requests[1]["bookId"] == "book-1"
    assert requests[1]["skill_version"] == "1.0.6"


@pytest.mark.parametrize("status,payload,expected", [
    (401, {}, "失效"),
    (429, {}, "频繁"),
    (200, {"errcode": -2012}, "失效"),
])
async def test_gateway_errors_are_actionable(status: int, payload: dict[str, object], expected: str) -> None:
    """status 与 payload 表示服务错误，expected 为用户可执行的提示。"""
    def respond(request: httpx.Request) -> httpx.Response:
        """为 request 返回指定错误。"""
        return httpx.Response(status, json=payload)

    provider = WereadProvider(Credentials(vid="reader", api_key="wrk-key"), httpx.MockTransport(respond))
    with pytest.raises(ProviderError, match=expected):
        await provider.get_bookshelf()


async def test_book_from_previous_account_is_not_requested() -> None:
    """当前账号不为上一账号的条目发送目录请求。"""
    def respond(request: httpx.Request) -> httpx.Response:
        """跨账号的 request 不应到达 HTTP 边界。"""
        raise AssertionError("不应请求另一账号的数据")

    provider = WereadProvider(Credentials(vid="new", api_key="wrk-key"), httpx.MockTransport(respond))
    with pytest.raises(ProviderError, match="账号已变化"):
        await provider.get_chapters(Book("old", "book-1", "旧账号书籍"))


async def test_api_key_only_accounts_have_separate_book_identities() -> None:
    """单独补充 API Key 时，不同密钥不能共用空账号标识。"""
    def respond(request: httpx.Request) -> httpx.Response:
        """为 request 返回相同书籍，验证身份来自凭据范围而非书名。"""
        return httpx.Response(200, json={"books": [{"bookId": "same-book", "title": "同一本书"}]})

    first = WereadProvider(Credentials(api_key="wrk-first"), httpx.MockTransport(respond))
    second = WereadProvider(Credentials(api_key="wrk-second"), httpx.MockTransport(respond))
    first_book = (await first.get_bookshelf())[0]
    second_book = (await second.get_bookshelf())[0]
    assert first_book.account_id != second_book.account_id
