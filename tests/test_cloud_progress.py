"""验证只读云端进度和实际阅读时间比较。"""

import json

import httpx
import pytest

from weread_tui.cloud_progress import CloudProgress, choose_progress
from weread_tui.credentials import Credentials
from weread_tui.models import Book
from weread_tui.models import Chapter, ChapterDocument
from weread_tui.positions import ContentAnchor
from weread_tui.progress import ReadingProgress
from weread_tui.provider import WereadProvider
from weread_tui.cloud_progress import cloud_anchor
from weread_tui.positions import PositionError
from weread_tui.source_positions import prepare_document


@pytest.mark.parametrize(("local_time", "cloud_time", "expected"), [
    (150.0, 100.0, "local"), (100.0, 150.0, "cloud"), (100.0, 100.0, "local"),
    (100.0, None, "local"), (0.0, 150.0, "local"), (100.0, 1000.0, "local"),
    (100.0, float("nan"), "local"),
])
def test_only_comparable_newer_cloud_reading_time_wins(
    local_time: float, cloud_time: float | None, expected: str,
) -> None:
    """local_time/cloud_time 是实际阅读时间；expected 不受请求时间影响。"""
    local = ReadingProgress("reader", "1", "2", ContentAnchor("t0", 3, "revision"), local_time)
    cloud = CloudProgress("reader", "1", "3", 20, cloud_time)
    assert choose_progress(local, cloud, now=200) == expected


def test_missing_cloud_or_local_record_uses_available_position() -> None:
    """云端不可用时保留本地，没有本地记录时可采用唯一的云端位置。"""
    local = ReadingProgress("reader", "1", "2", ContentAnchor("t0", 3, "revision"), 100)
    cloud = CloudProgress("reader", "1", "3", 20, 150)
    assert choose_progress(local, None, now=200) == "local"
    assert choose_progress(None, cloud, now=200) == "cloud"
    assert choose_progress(None, None, now=200) is None


async def test_official_progress_uses_read_time_not_response_timestamp() -> None:
    """HTTP 边界只允许进度查询，顶层 timestamp 不得变成位置更新时间。"""
    def respond(request: httpx.Request) -> httpx.Response:
        """request 必须是官方只读业务接口的平铺请求。"""
        assert json.loads(request.content) == {
            "api_name": "/book/getprogress", "skill_version": "1.0.6", "bookId": "1",
        }
        return httpx.Response(200, json={"bookId": "1", "timestamp": 9999,
            "book": {"chapterUid": 3, "chapterOffset": 20, "updateTime": 150}})

    provider = WereadProvider(Credentials(vid="reader", api_key="wrk-test"), httpx.MockTransport(respond))
    result = await provider.get_cloud_progress(Book("reader", "1", "书"))
    assert result == CloudProgress("reader", "1", "3", 20, 150)


@pytest.mark.parametrize(("source", "format_name", "offset", "raw_offset", "text_offset"), [
    ("\ufeff<p>甲😀&amp;锚</p>", "epub", 12, 11, 3),
    ("甲\r\n😀锚", "txt", 5, 4, 3),
])
def test_cloud_utf16_coordinates_keep_full_source_and_unicode_mapping(
    source: str, format_name: str, offset: int, raw_offset: int, text_offset: int,
) -> None:
    """source/format_name 为手算样本；offset 含标签实体，预期 raw_offset/text_offset 采用码点。"""
    document = ChapterDocument(Book("reader", "1", "书"), Chapter("2", "章"), source, format_name)
    result = cloud_anchor(document, CloudProgress("reader", "1", "2", offset, 150))
    assert result.text_offset == text_offset
    assert prepare_document(source, format_name).source_offset(result) == raw_offset


@pytest.mark.parametrize("offset", [-1, 6, 1000])
def test_invalid_or_split_surrogate_cloud_offsets_fail(offset: int) -> None:
    """offset 不能越界或落在 emoji 的 UTF-16 代理对中间。"""
    document = ChapterDocument(Book("reader", "1", "书"), Chapter("2", "章"), "\ufeff<p>甲😀锚</p>", "epub")
    with pytest.raises(PositionError):
        cloud_anchor(document, CloudProgress("reader", "1", "2", offset, 150))


def test_offset_in_head_does_not_fall_forward_to_first_visible_character() -> None:
    """正文外偏移不能被宽松源映射悄悄改成章首并宣称准确恢复。"""
    document = ChapterDocument(Book("reader", "1", "书"), Chapter("2", "章"),
                               "<html><head><style>p{color:red}</style></head><body><p>正文</p></body></html>", "epub")
    with pytest.raises(PositionError, match="不在章节正文"):
        cloud_anchor(document, CloudProgress("reader", "1", "2", document.source.index("color"), 150))
