"""网页正文线格式；数学规则与来源见协议观察记录。"""

import base64
import binascii
from hashlib import md5
from urllib.parse import quote


class ContentError(ValueError):
    """正文完整性或协议格式不符合预期。"""


def encode_id(value: str) -> str:
    """把原始 value 转为网页标识，不改变目录中的原始身份。"""
    digest = md5(value.encode()).hexdigest()
    numeric = value.isascii() and value.isdecimal()
    groups = ([f"{int(value[index:index + 9]):x}" for index in range(0, len(value), 9)]
              if numeric else ["".join(f"{byte:x}" for byte in value.encode())])
    prefix = digest[:3] + ("3" if numeric else "4") + "2" + digest[-2:]
    encoded = prefix + "g".join(f"{len(group):02x}{group}" for group in groups)
    encoded += digest[:max(0, 20 - len(encoded))]
    return encoded + md5(encoded.encode()).hexdigest()[:3]


def signature(fields: dict[str, str | int | bool]) -> str:
    """按字段名排序并签署 fields，s 字段不参与计算。"""
    parts = []
    for key, value in sorted(fields.items()):
        if key != "s":
            text = str(value).lower() if isinstance(value, bool) else str(value)
            parts.append(f"{key}={quote(text, safe='-_.~')}")
    payload = "&".join(parts).encode()
    left = right = 0x15051505
    for index in range(len(payload) - 1, 0, -2):
        left = (left ^ (payload[index] << ((len(payload) - index) % 30))) & 0x7FFFFFFF
        right = (right ^ (payload[index - 1] << (index % 30))) & 0x7FFFFFFF
    return f"{left + right:x}"


def verify_fragment(fragment: str) -> str:
    """校验 fragment 的 MD5 前缀，成功后只返回载荷。"""
    digest, payload = fragment[:32], fragment[32:]
    if len(digest) != 32 or digest.lower() != md5(payload.encode()).hexdigest():
        raise ContentError("正文分片校验失败，请重新加载章节。")
    return payload


def decode_content(payload: str) -> str:
    """对合并后的 payload 逆交换并解码，拒绝非法编码而非替换乱码。"""
    characters = list(payload[1:])
    length = len(characters)
    positions: list[int] = []
    if 4 <= length < 11:
        positions = [0, 2]
    elif length >= 11:
        tail_size = min(4, (length + 9) // 10)
        digits = "".join(str(int(f"{ord(character):b}", 4)) for character in reversed(characters[-tail_size:]))
        modulus = length - tail_size - 2
        width = len(str(modulus))
        for start in range(0, len(digits) - width, width):
            positions.extend([int(digits[start:start + width]) % modulus,
                              int(digits[start + 1:start + width + 1]) % modulus])
            if len(positions) == 10:
                break
    # 成对交换必须逆序还原，相邻的两字节也按逆序交换。
    for index in range(len(positions) - 2, -1, -2):
        left, right = positions[index:index + 2]
        for offset in (1, 0):
            characters[left + offset], characters[right + offset] = characters[right + offset], characters[left + offset]
    encoded = "".join("".join(characters).split()).replace("-", "+").replace("_", "/")
    try:
        return base64.b64decode(encoded + "=" * (-len(encoded) % 4), validate=True).decode("utf-8")
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise ContentError("正文编码无效，可能是分片版本不一致或协议已变化。") from None
