"""WeasyPrint 70 布局树的单一位置适配器。"""

from typing import Any

from weasyprint.formatting_structure.boxes import ReplacedBox, TextBox  # type: ignore[import-untyped]

from weread_tui.positions import LAYOUT_WHITESPACE, PositionError


def page_spans(document: Any, runs: dict[str, str]) -> list[dict[str, str | int]]:
    """把 document 最终文本片段逐一对齐到 runs，不依据页比例猜测字符。"""
    cursors = {run_id: 0 for run_id in runs}
    spans: list[dict[str, str | int]] = []
    for page_index, page in enumerate(document.pages):
        for box in page._page_box.descendants():
            if box.element is None:
                continue
            run_id = box.element.get("data-weread-run")
            if run_id not in runs or any(pseudo in (box.element_tag or "") for pseudo in ("::before", "::after")):
                continue
            if isinstance(box, ReplacedBox) and run_id.startswith("i"):
                spans.append({"run_id": run_id, "start": 0, "end": 1, "page": page_index})
            elif isinstance(box, TextBox):
                text = runs[run_id]
                cursor = cursors[run_id]
                start: int | None = None
                preserve_spaces = box.style["white_space"] in ("pre", "pre-wrap", "break-spaces")
                for character in box.text:
                    if character in LAYOUT_WHITESPACE and not preserve_spaces:
                        continue
                    while (cursor < len(text) and text[cursor] in LAYOUT_WHITESPACE
                           and (not preserve_spaces or text[cursor] != character)):
                        cursor += 1
                    if cursor >= len(text) or text[cursor] != character:
                        raise PositionError("排版字符与原文不一致，无法保证阅读位置。")
                    if start is None:
                        start = cursor
                    cursor += 1
                cursors[run_id] = cursor
                if start is not None:
                    spans.append({"run_id": run_id, "start": start, "end": cursor, "page": page_index})
    return spans
