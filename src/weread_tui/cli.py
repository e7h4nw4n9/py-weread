"""命令行入口。"""

import typer

def launch() -> None:
    """启动微信读书终端阅读器。"""
    # 帮助命令不导入图像组件，终端能力探测仅发生在真正启动界面之前。
    from weread_tui.app import WereadApp
    from weread_tui.terminal import terminal_capabilities

    terminal_capabilities()
    WereadApp().run()


def main() -> None:
    """解析命令行参数并启动应用。"""
    typer.run(launch)
