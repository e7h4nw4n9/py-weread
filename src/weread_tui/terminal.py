"""隔离已锁版本的图像协议及终端像素尺寸查询。"""

from dataclasses import dataclass

from textual.geometry import Size
from textual_image._terminal import CellSize, get_cell_size
from textual_image._terminal import get_tiocgwinsz  # type: ignore[attr-defined]  # 平台函数未显式导出。
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


def refresh_terminal_capabilities(size: Size | None = None, pixel_size: Size | None = None) -> TerminalCapabilities | None:
    """用 size/pixel_size 或只读 ioctl 更新像素缓存，不发送查询或读取标准输入。"""
    if size is not None and pixel_size is not None:
        columns, rows = size
        pixel_width, pixel_height = pixel_size
    else:
        try:
            rows, columns, pixel_width, pixel_height = get_tiocgwinsz()
        except OSError:
            return None
    if min(columns, rows, pixel_width, pixel_height) <= 0:
        return None
    cell = CellSize(pixel_width // columns, pixel_height // rows)
    if min(cell) <= 0:
        return None
    # textual-image 0.13.2 的所有图像控件共享此缓存，兼容操作集中于本模块。
    setattr(get_cell_size, "_result", cell)
    return TerminalCapabilities(cell.width, cell.height, Image in (TGPRenderable, SixelRenderable))
