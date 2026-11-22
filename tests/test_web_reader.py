"""在真实 HTTP 边界模拟正常响应和明确拒绝。"""

import json
from hashlib import md5

import httpx
import pytest

from weread_tui.web_reader import fetch_chapter
from weread_tui.chapter_protocol import ContentError
from weread_tui.web_reader import WebSessionExpired


async def test_txt_chapter_joins_parts_before_decoding() -> None:
    """TXT 元数据不加入正文，t_1 的合法空对象不当作网络失败。"""
    def respond(request: httpx.Request) -> httpx.Response:
        """为 request 返回独立手算的短正文样本。"""
        if request.method == "GET":
            state = {"reader": {"psvts": "state", "bookInfo": {"bookId": "1"},
                                "currentChapter": {"chapterUid": 2}}}
            return httpx.Response(200, text=f"<script>window.__INITIAL_STATE__={json.dumps(state)};</script>")
        data = json.loads(request.content)
        assert data["ps"] == "state"
        assert data["prevChapter"] is False
        assert "s" in data and "pc" in data
        if request.url.path.endswith("e_0"):
            return httpx.Response(200, json={"bookId": "1", "chapterUid": 2})
        if request.url.path.endswith("t_0"):
            payload = "!QkJDRA=="
            return httpx.Response(200, text=md5(payload.encode()).hexdigest() + payload)
        assert request.url.path.endswith("t_1")
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(base_url="https://weread.qq.com", transport=httpx.MockTransport(respond)) as client:
        source, format_name = await fetch_chapter(client, "1", "2")
    assert source == "$4$D"
    assert format_name == "txt"


async def test_denied_chapter_is_not_decoded_or_retried() -> None:
    """HTTP 403 表示拒绝访问，不尝试改变参数再次请求。"""
    def respond(request: httpx.Request) -> httpx.Response:
        """拒绝 request，不提供任何正文分片。"""
        return httpx.Response(403)

    async with httpx.AsyncClient(base_url="https://weread.qq.com", transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ContentError, match="访问"):
            await fetch_chapter(client, "1", "2")


@pytest.mark.parametrize("payloads,expected", [
    (["a", "bbbb", "c"], "版本"),
    (["!AAAAA", "AAAAAA", "AAAAA"], "XML"),
])
async def test_inconsistent_epub_parts_are_not_displayed(payloads: list[str], expected: str) -> None:
    """payloads 为独立有效 MD5 的错误分片组，expected 为完整性错误原因。"""
    def respond(request: httpx.Request) -> httpx.Response:
        """按 request 返回单片有效但整组不可用的内容。"""
        if request.method == "GET":
            state = {"reader": {"psvts": "state", "bookInfo": {"bookId": "1"},
                                "currentChapter": {"chapterUid": 2}}}
            return httpx.Response(200, text=f"<script>window.__INITIAL_STATE__={json.dumps(state)};</script>")
        index = {"e_0": 0, "e_1": 1, "e_3": 2}[request.url.path.rsplit("/", 1)[1]]
        payload = payloads[index]
        return httpx.Response(200, text=md5(payload.encode()).hexdigest() + payload)

    async with httpx.AsyncClient(base_url="https://weread.qq.com", transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ContentError, match=expected):
            await fetch_chapter(client, "1", "2")


async def test_unknown_service_error_does_not_expire_web_session() -> None:
    """二维码端点的 -2013 不能直接套用到章节正文端点。"""
    def respond(request: httpx.Request) -> httpx.Response:
        """对 request 返回未知正文拒绝码。"""
        if request.method == "GET":
            state = {"reader": {"psvts": "state", "bookInfo": {"bookId": "1"},
                                "currentChapter": {"chapterUid": 2}}}
            return httpx.Response(200, text=f"<script>window.__INITIAL_STATE__={json.dumps(state)};</script>")
        return httpx.Response(200, json={"errCode": -2013})

    async with httpx.AsyncClient(base_url="https://weread.qq.com", transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ContentError) as error:
            await fetch_chapter(client, "1", "2")
    assert not isinstance(error.value, WebSessionExpired)
