"""命令行入口。"""

import typer

def launch() -> None:
    """启动微信读书终端阅读器。"""
    # 帮助命令不导入图像组件，终端能力探测仅发生在真正启动界面之前。
    from weread_tui.app import WereadApp
    from weread_tui.terminal import terminal_capabilities
    from weread_tui.progress import ProgressStore
    from pathlib import Path

    terminal_capabilities()
    progress = ProgressStore(Path.home() / ".local/share/weread-tui/progress.sqlite3")
    WereadApp(progress=progress).run()


def main() -> None:
    """解析命令行参数并启动应用。"""
    typer.run(launch)
