"""用独立手算向量验证网页正文的线格式，不使用真实书籍内容。"""

import pytest

from weread_tui.chapter_protocol import ContentError, decode_content, encode_id, signature, verify_fragment


def test_numeric_identifier_uses_documented_representation() -> None:
    """标识 1 的 MD5、长度与校验后缀采用独立计算的固定值。"""
    assert encode_id("1") == "c4c329b011c4ca4238a0201"


def test_empty_signature_has_known_accumulator_value() -> None:
    """空字段签名等于两个初始累积值之和。"""
    assert signature({}) == "2a0a2a0a"


def test_short_payload_decodes_after_pair_swap() -> None:
    """手算向量覆盖去除前缀、相邻字符交换及 Base64 解码。"""
    assert decode_content("!QkJDRA==") == "$4$D"


def test_corrupted_fragment_is_rejected() -> None:
    """相同 MD5 前缀不能接受被修改的正文。"""
    assert verify_fragment("900150983CD24FB0D6963F7D28E17F72abc") == "abc"
    with pytest.raises(ContentError, match="校验"):
        verify_fragment("900150983CD24FB0D6963F7D28E17F72abd")


def test_non_utf8_content_is_rejected() -> None:
    """该载荷还原为 /w==，即非法 UTF-8 字节 FF。"""
    with pytest.raises(ContentError, match="编码"):
        decode_content("!==/w")
