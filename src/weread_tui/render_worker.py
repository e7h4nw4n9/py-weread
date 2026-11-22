"""单次图形任务子进程，通过 JSON 输入输出与界面隔离。"""

import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any


def rasterize(directory: Path, index: int) -> None:
    """把 directory 中 PDF 的 index 页转换为每 CSS 像素一个栅格像素。"""
    import pypdfium2 as pdfium  # type: ignore[import-untyped]

    with pdfium.PdfDocument(directory / "chapter.pdf") as document:
        page = document[index]
        bitmap = page.render(scale=4 / 3)
        image = bitmap.to_pil()
        try:
            path = directory / f"page-{index}.png"
            temporary = path.with_suffix(".tmp")
            image.save(temporary, format="PNG")
            temporary.replace(path)
        finally:
            image.close()
            bitmap.close()
            page.close()


def main() -> None:
    """执行一份任务；原生库错误通过 JSON 返回，不让界面进程崩溃。"""
    job = json.load(sys.stdin)
    try:
        result: dict[str, Any] = {}
        # 原生库的导入提示和日志不能混入 stdout 上的 JSON 通信。
        with redirect_stdout(sys.stderr):
            if job["action"] == "paginate":
                from weread_tui.native_render import paginate

                count = paginate(job)
                for index in range(min(2, count)):
                    rasterize(Path(job["directory"]), index)
                result["page_count"] = count
            else:
                rasterize(Path(job["directory"]), int(job["index"]))
        print(json.dumps(result), flush=True)
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        print(json.dumps({"error": str(error)}), flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
