"""锁定 textual-image 尺寸缓存的兼容契约。"""

from typing import Any

import pytest
from textual.geometry import Size
from textual_image import _terminal

from weread_tui import terminal


def test_ioctl_refresh_updates_image_cache_without_reading_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """monkeypatch 只替换系统尺寸及输入边界，运行中的刷新不能读取标准输入。"""
    monkeypatch.setattr(_terminal.get_cell_size, "_result", _terminal.CellSize(8, 16), raising=False)
    monkeypatch.setattr(terminal, "get_tiocgwinsz", lambda: (30, 80, 960, 720))

    def forbidden_input(*args: Any, **kwargs: Any) -> Any:
        """args/kwargs 代表终端查询参数，此路径不允许被运行中的刷新调用。"""
        raise AssertionError("刷新尺寸时不得读取终端输入")

    monkeypatch.setattr(_terminal, "read", forbidden_input)
    monkeypatch.setattr(_terminal, "capture_terminal_response", forbidden_input)
    result = terminal.refresh_terminal_capabilities()
    assert result is not None
    assert (result.cell_width, result.cell_height) == (12, 24)
    assert _terminal.get_cell_size() == _terminal.CellSize(12, 24)


def test_unknown_pixel_size_keeps_existing_image_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """monkeypatch 固定既有有效缓存；未知像素不能破坏已知单元格尺寸。"""
    monkeypatch.setattr(_terminal.get_cell_size, "_result", _terminal.CellSize(8, 16), raising=False)
    monkeypatch.setattr(terminal, "get_tiocgwinsz", lambda: (30, 80, 0, 0))
    assert terminal.refresh_terminal_capabilities(Size(80, 30), Size(0, 0)) is None
    assert _terminal.get_cell_size() == _terminal.CellSize(8, 16)
