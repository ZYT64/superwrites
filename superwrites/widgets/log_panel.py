"""右下日志面板。

基于 RichLog，支持：
- 多级别（info / success / warning / error / ai）
- 自动换行（RichLog 原生）
- 自动滚动到底部
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import RichLog


class LogPanel(RichLog):
    """右下日志面板。"""

    DEFAULT_CSS = """
    LogPanel {
        width: 100%;
        height: 1fr;
        border: solid $border-blurred;
        border-bottom: none;
        scrollbar-size-vertical: 0;
        scrollbar-size-horizontal: 0;
    }
    LogPanel:focus-within {
        border: solid $primary;
        border-bottom: none;
    }
    """

    def __init__(self) -> None:
        super().__init__(
            id="log-panel",
            highlight=False,
            markup=True,
            wrap=True,
            max_lines=2000,
            auto_scroll=True,
        )

    def info(self, msg: str) -> None:
        self.write(Text(f"  {msg}", style="dim"))

    def success(self, msg: str) -> None:
        self.write(Text(f"[OK] {msg}", style="green"))

    def warning(self, msg: str) -> None:
        self.write(Text(f"[!] {msg}", style="yellow"))

    def error(self, msg: str) -> None:
        self.write(Text(f"[X] {msg}", style="bold red"))

    def ai(self, msg: str) -> None:
        """AI 输出内容（默认品红色，标识是模型生成）。"""
        self.write(Text(msg, style="magenta"))

    def progress(self, current: int, total: int, label: str = "") -> None:
        """简易进度条（用方块字符）。"""
        bar_width = 20
        ratio = current / max(total, 1)
        filled = int(bar_width * ratio)
        bar = "█" * filled + "░" * (bar_width - filled)
        self.write(Text(f"  [{bar}] {current}/{total} {label}", style="blue"))