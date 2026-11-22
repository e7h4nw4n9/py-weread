"""通过已安装的命令入口验证用户可见行为。"""

import subprocess
import sys
from pathlib import Path


def test_installed_command_shows_help_without_starting_ui() -> None:
    """帮助命令无需交互终端即可成功退出。"""
    command = Path(sys.executable).parent / "weread"
    result = subprocess.run(
        [str(command), "--help"], capture_output=True, text=True, timeout=10
    )

    assert result.returncode == 0, result.stderr
    assert "微信读书" in result.stdout
    assert "--help" in result.stdout
