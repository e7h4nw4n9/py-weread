"""隔离已锁版本的图像协议及终端像素尺寸查询。"""

from dataclasses import dataclass

from textual_image._terminal import get_cell_size
from textual_image.renderable import Image
from textual_image.renderable.sixel import Image as SixelRenderable
from textual_image.renderable.tgp import Image as TGPRenderable


@dataclass(frozen=True)
class TerminalCapabilities:
    """cell_width/cell_height 为单元格像素，graphics 表示图像协议探测结果。"""

    cell_width: int
    cell_height: int
    graphics: bool


def terminal_capabilities() -> TerminalCapabilities:
    """读取启动前已探测并缓存的结果，不在运行中的 Textual 抢读输入。"""
    cell = get_cell_size()
    return TerminalCapabilities(cell.width, cell.height, Image in (TGPRenderable, SixelRenderable))
