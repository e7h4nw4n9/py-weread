"""读取当前网页会话有权访问的章节，不改变服务端权限判断。"""

import json
import re
from secrets import randbelow
from time import time
from typing import Any

import httpx

from weread_tui.auth import WEB_ORIGIN
from weread_tui.chapter_protocol import ContentError, decode_content, encode_id, signature, verify_fragment


class WebSessionExpired(ContentError):
    """服务端明确拒绝网页会话，需要重新扫码。"""


def check_response(response: httpx.Response) -> None:
    """区分 response 的登录失效和访问拒绝，其他 HTTP 错误由上层显示。"""
    if response.status_code == 401:
        raise WebSessionExpired("网页会话已失效，请重新扫码登录。")
    if response.status_code == 403:
        raise ContentError("该章节被拒绝访问，请在官方网页端确认阅读权限。")
    response.raise_for_status()


def check_service_error(data: dict[str, Any]) -> None:
    """识别正文端点在 HTTP 成功响应 data 中返回的错误。"""
    code = data.get("errCode", data.get("errcode", 0))
    if code in (-2010, -2012):
        raise WebSessionExpired("网页会话已失效，请重新扫码登录。")
    if code:
        raise ContentError(f"正文服务拒绝请求（{code}），请检查官方网页端的访问权限。")


def reader_state(html: str, book_id: str, chapter_uid: str) -> dict[str, Any]:
    """只解析 html 内嵌 JSON，并确认其确实属于 book_id/chapter_uid。"""
    marker = re.search(r"window\.__INITIAL_STATE__\s*=\s*", html)
    if marker is None:
        raise ContentError("未找到网页阅读状态，可能是访问受限或网页接口已变化。")
    try:
        state, _ = json.JSONDecoder().raw_decode(html[marker.end():].lstrip())
        reader: dict[str, Any] = state["reader"]
        if str(reader["bookInfo"]["bookId"]) != book_id or str(reader["currentChapter"]["chapterUid"]) != chapter_uid:
            raise ContentError("网页返回了其他书籍或章节，未显示错误位置的内容。")
        if not isinstance(reader["psvts"], str) or not reader["psvts"]:
            raise ContentError("当前章节的网页状态不可用，请确认网页端可正常阅读。")
        return reader
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, ContentError):
            raise
        raise ContentError("网页阅读状态格式已变化，无法读取当前章节。") from None


async def chapter_part(client: httpx.AsyncClient, endpoint: str, book_id: str, chapter_uid: str, ps: str, referer: str) -> str | dict[str, Any]:
    """用当前章节的 ps 请求 endpoint；referer 对应当前章节页面。"""
    timestamp = int(time())
    if encode_id(str(timestamp)) == ps:
        timestamp += 1
    fields: dict[str, str | int | bool] = {
        "b": encode_id(book_id), "c": encode_id(chapter_uid), "ps": ps,
        "ct": str(timestamp), "pc": encode_id(str(timestamp)),
        "r": str(randbelow(10000) ** 2), "sc": 0 if endpoint in ("e_3", "t_1") else 1,
        "prevChapter": False, "st": 1 if endpoint == "e_2" else 0,
    }
    fields["s"] = signature(fields)
    response = await client.post(f"/web/book/chapter/{endpoint}", json=fields, headers={"Referer": referer})
    check_response(response)
    text = response.text
    if text.lstrip().startswith("{"):
        data: dict[str, Any] = response.json()
        check_service_error(data)
        return data
    return verify_fragment(text)


async def fetch_chapter(client: httpx.AsyncClient, book_id: str, chapter_uid: str) -> tuple[str, str]:
    """获取 book_id/chapter_uid 的正文与格式；client 仅使用本人授权会话。"""
    path = f"/web/reader/{encode_id(book_id)}k{encode_id(chapter_uid)}"
    referer = WEB_ORIGIN + path
    try:
        response = await client.get(path)
        check_response(response)
        state = reader_state(response.text, book_id, chapter_uid)
        ps = state["psvts"]
        first = await chapter_part(client, "e_0", book_id, chapter_uid, ps, referer)
        format_name = "epub"
        parts: list[str] = []
        if isinstance(first, dict):
            if not any(key in first for key in ("bookId", "chapterUid", "title")):
                raise ContentError("正文服务返回了未支持的响应格式。")
            format_name = "txt"
            endpoints = ("t_0", "t_1")
        else:
            parts.append(first)
            endpoints = ("e_1", "e_3")
        for endpoint in endpoints:
            part = await chapter_part(client, endpoint, book_id, chapter_uid, ps, referer)
            if part == {} and endpoint == "t_1":
                continue
            if not isinstance(part, str):
                raise ContentError("正文分片缺失，未输出不完整章节。")
            parts.append(part)
        if format_name == "epub" and max(map(len, parts)) - min(map(len, parts)) > 2:
            raise ContentError("正文分片长度不一致，可能来自不同版本，请重新加载。")
        source = decode_content("".join(parts))
        if format_name == "epub" and any(ord(character) < 32 and character not in "\t\n\r" for character in source):
            raise ContentError("正文包含非法 XML 字符，可能存在分片版本不一致。")
        return source, format_name
    except httpx.HTTPError:
        raise ContentError("正文请求失败，请检查网络后重新加载。") from None
    except (json.JSONDecodeError, UnicodeError):
        raise ContentError("正文响应编码或结构异常，请重新加载。") from None


async def fetch_stylesheet(client: httpx.AsyncClient, book_id: str, chapter_uid: str) -> str:
    """读取 book_id/chapter_uid 的原书 CSS，不把样式分片并入正文。"""
    path = f"/web/reader/{encode_id(book_id)}k{encode_id(chapter_uid)}"
    try:
        response = await client.get(path)
        check_response(response)
        state = reader_state(response.text, book_id, chapter_uid)
        part = await chapter_part(client, "e_2", book_id, chapter_uid, state["psvts"], WEB_ORIGIN + path)
        if part == {}:
            return ""
        if not isinstance(part, str):
            raise ContentError("当前章节未返回可用的原书样式。")
        return decode_content(part)
    except (httpx.HTTPError, json.JSONDecodeError):
        raise ContentError("原书样式暂时无法读取。") from None
