"""底部命令输入条。原生 Textual Suggester 命令补全。"""

from __future__ import annotations

from textual.suggester import Suggester
from textual.widgets import Input


class CommandSuggester(Suggester):
    """根据已有输入前缀补全命令和参数。"""

    # 完整命令模板列表（带描述）— 按"参数完整度"排序
    COMMANDS = [
        "help",
        "setup",
        "model",
        "export txt",
        "export epub",
        "write 1 2000",
        "write 1-3 2000",
        "write 1",
        "write 1-3",
    ]

    async def get_suggestion(self, value: str) -> str | None:
        if not value:
            return None
        v = value.strip()
        # 用户输入与模板开始匹配：返回第一个比当前更长的匹配项
        for cmd in self.COMMANDS:
            if cmd == v:
                continue
            if cmd.startswith(v) and len(cmd) > len(v):
                return cmd
        # 用户已经输入完整 N 或 N-M，提示加字数参数
        if v.startswith("write "):
            arg = v[6:].strip()
            if arg.isdigit() or (arg and "-" in arg and all(p.isdigit() for p in arg.split("-", 1))):
                return f"{v} 2000"
        return None


class InputBar(Input):
    """底部命令输入条。Tab 接受命令补全。"""

    DEFAULT_CSS = """
    InputBar {
        width: 100%;
        height: 3;
        border: solid $border-blurred;
        border-top: none;
    }
    InputBar:focus {
        border: solid $primary;
        border-top: none;
    }
    """

    BINDINGS = [
        # Esc 清空输入框（Textual 默认无此行为）
    ]

    def __init__(self) -> None:
        super().__init__(
            placeholder="命令 write 3 / write 1-3 2000 / setup / model / help（Tab 补全）",
            suggester=CommandSuggester(),
            id="input-bar",
        )

    async def on_key(self, event) -> None:
        """Esc 清空输入框。"""
        if event.key == "escape":
            if self.value:
                self.value = ""
                event.prevent_default()
                event.stop()