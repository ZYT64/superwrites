"""SuperWrites 主程序：Textual TUI 应用。

布局（与需求文档一致）：
┌──────────────────────────────────────────────────────────────────┐
│ 顶部状态栏：小说名 / 总章节 / 已完成 / 当前模型                    │
├──────────────┬───────────────────────────────────────────────────┤
│ 左侧大纲树    │ 右上预览面板                                       │
│ (30%)         │ (70% × 60%)                                       │
│              ├───────────────────────────────────────────────────┤
│              │ 右下日志 + 输入框（70% × 40%）                       │
└──────────────┴───────────────────────────────────────────────────┘

快捷键：
- Tab / 方向键：切换焦点
- Enter（左侧选中章节）：生成 / 重新生成
- Ctrl+S：保存
- Ctrl+E：导出
- Ctrl+N：新建项目
- Ctrl+O：打开项目
- Ctrl+Q：退出
- /：聚焦输入框
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Footer, Input, Static

from .core import config_manager
from .core.ai_client import AIError, AIClient
from .core.exporter import export_epub, export_txt
from .core.novel_engine import (
    CHAPTER_STATUS_DONE,
    CHAPTER_STATUS_PENDING,
    CHAPTER_STATUS_WRITING,
    NovelEngine,
    NovelMeta,
    init_project,
    load_meta,
    project_path,
    save_meta,
)
from .widgets.input_bar import InputBar
from .widgets.log_panel import LogPanel
from .widgets.preview_panel import PreviewPanel
from .widgets.sidebar import SidebarTree


# 命令格式：/cmd args 或 cmd args（都接受，因为输入框专用于命令）
_CMD_RE = re.compile(r"^/?\s*(\w+)(?:\s+(.*))?$")


# =========================================================================
# Modal Screens：用于弹窗输入
# =========================================================================


class StringPrompt(ModalScreen[str | None]):
    """简单的字符串输入弹窗。"""

    BINDINGS = [Binding("escape", "cancel", "取消")]

    DEFAULT_CSS = """
    StringPrompt {
        align: center middle;
    }
    StringPrompt > Vertical {
        width: 70%;
        height: auto;
        border: thick $primary;
        background: $surface;
    }
    StringPrompt .modal-title {
        height: 1;
        background: $primary;
        color: $foreground;
        text-style: bold;
        padding: 0 1;
    }
    StringPrompt #label {
        height: auto;
        margin: 1 0;
        padding: 0 1;
        color: $foreground-muted;
    }
    """

    def __init__(self, label: str, default: str = "", title: str = "输入") -> None:
        super().__init__()
        self._label = label
        self._default = default
        self._title = title

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._title, classes="modal-title")
            yield Static(self._label, id="label")
            yield Input(value=self._default, id="inp")

    def on_mount(self) -> None:
        self.query_one("#inp", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)


class IntPrompt(ModalScreen[int | None]):
    """简单的整数输入弹窗。"""

    BINDINGS = [Binding("escape", "cancel", "取消")]

    DEFAULT_CSS = """
    IntPrompt {
        align: center middle;
    }
    IntPrompt > Vertical {
        width: 60%;
        height: auto;
        border: thick $primary;
        background: $surface;
    }
    IntPrompt .modal-title {
        height: 1;
        background: $primary;
        color: $foreground;
        text-style: bold;
        padding: 0 1;
    }
    IntPrompt #label {
        height: auto;
        margin: 1 0;
        padding: 0 1;
        color: $foreground-muted;
    }
    """

    def __init__(self, label: str, default: int = 1,
                 min_v: int = 1, max_v: int = 999, title: str = "输入数字") -> None:
        super().__init__()
        self._label = label
        self._default = default
        self._min = min_v
        self._max = max_v
        self._title = title

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._title, classes="modal-title")
            yield Static(self._label, id="label")
            yield Input(value=str(self._default), id="inp")

    def on_mount(self) -> None:
        self.query_one("#inp", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        try:
            v = int(event.value.strip())
        except ValueError:
            self.dismiss(None)
            return
        v = max(self._min, min(self._max, v))
        self.dismiss(v)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ProjectPicker(ModalScreen[str | None]):
    """通用列表选择弹窗。支持键盘 ↑↓ 选择、数字、或直接输入。"""

    BINDINGS = [
        Binding("escape", "cancel", "取消"),
        Binding("up", "cursor_up", "", show=False),
        Binding("down", "cursor_down", "", show=False),
        Binding("enter", "select_focused", "确定", show=False),
    ]

    DEFAULT_CSS = """
    ProjectPicker {
        align: center middle;
    }
    ProjectPicker > Vertical {
        width: 85%;
        height: 85%;
        border: thick $primary;
        background: $surface;
    }
    ProjectPicker .modal-title {
        height: 1;
        background: $primary;
        color: $foreground;
        text-style: bold;
        padding: 0 1;
    }
    ProjectPicker #label {
        height: auto;
        margin: 1 0;
        padding: 0 1;
        color: $foreground-muted;
    }
    ProjectPicker .item-list {
        height: 1fr;
        padding: 0 1;
        overflow-y: auto;
        scrollbar-size-vertical: 0;
        scrollbar-size-horizontal: 0;
    }
    ProjectPicker .item-list Static {
        height: auto;
        padding: 0 1;
    }
    ProjectPicker .item-list .cursor {
        background: $accent;
        color: $foreground;
    }
    """

    def __init__(self, label: str, items: list[str], default: int = 1, title: str = "选择") -> None:
        super().__init__()
        self._label = label
        self._items = items
        self._default = default
        self._title = title
        self._cursor = 0  # 当前高亮索引

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._title, classes="modal-title")
            yield Static(self._label, id="label")
            with Vertical(classes="item-list", id="items-container"):
                for i, name in enumerate(self._items, 1):
                    text = name.strip()
                    if not text:
                        continue
                    yield Static(f"  [{i}] {text}", id=f"item-{i}")
            yield Input(
                placeholder=f"输入 1-{len(self._items)} 选择，↑↓ 移动，回车确认，或直接输入自定义内容",
                id="inp",
            )

    def on_mount(self) -> None:
        self._highlight()

    def _highlight(self) -> None:
        for i, w in enumerate(self.query(".item-list Static")):
            cls = "cursor" if i == self._cursor else ""
            w.set_class(i == self._cursor, "cursor")

    def _pick(self, idx: int) -> None:
        if 0 <= idx < len(self._items):
            self.dismiss(self._items[idx].strip())

    def action_cursor_up(self) -> None:
        if self._items:
            self._cursor = (self._cursor - 1) % len(self._items)
            self._highlight()

    def action_cursor_down(self) -> None:
        if self._items:
            self._cursor = (self._cursor + 1) % len(self._items)
            self._highlight()

    def action_select_focused(self) -> None:
        self._pick(self._cursor)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        val = event.value.strip()
        try:
            idx = int(val)
            if 1 <= idx <= len(self._items):
                self.dismiss(self._items[idx - 1].strip())
                return
        except ValueError:
            pass
        if val:
            self.dismiss(val)
        else:
            self._pick(self._cursor)

    def action_cancel(self) -> None:
        self.dismiss(None)


# =========================================================================
# 主应用
# =========================================================================


class SuperWritesApp(App):
    """SuperWrites TUI 主应用。"""

    TITLE = "SuperWrites · AI 网文生成器"
    SUB_TITLE = "TUI 网文创作伙伴"

    CSS = """
    Screen {
        background: $surface;
        scrollbar-size-vertical: 0;
        scrollbar-size-horizontal: 0;
    }
    #status-bar {
        height: 1;
        dock: top;
        background: $boost;
        color: $foreground-muted;
        text-style: bold;
        padding: 0 1;
    }
    #main-area {
        height: 1fr;
    }
    #sidebar-container {
        width: 30%;
        border-right: solid $border-blurred;
    }
    #right-area {
        width: 1fr;
    }
    #right-top {
        height: 60%;
        border-bottom: solid $border-blurred;
    }
    #right-bottom {
        height: 40%;
    }
    ModalScreen > Vertical {
        border: thick $primary;
        background: $surface;
    }
    ModalScreen .modal-title {
        height: 1;
        background: $primary;
        color: $foreground;
        text-style: bold;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+s", "save_all", "保存", show=True, priority=True),
        Binding("ctrl+e", "export", "导出", show=True, priority=True),
        Binding("ctrl+n", "new_project", "新建", show=True, priority=True),
        Binding("ctrl+o", "open_project", "打开", show=True, priority=True),
        Binding("ctrl+q", "quit", "退出", show=True, priority=True),
        Binding("slash", "focus_input", "命令", show=True, priority=True),
    ]

    async def on_key(self, event) -> None:
        """全局 Esc 关闭顶层 modal（CommandPalette 等）。"""
        if event.key == "escape" and self.screen_stack:
            top = self.screen_stack[-1]
            # CommandPalette 继承自 SystemModalScreen
            if hasattr(top, "dismiss") and top.__class__.__name__ != "Screen":
                top.dismiss(None)
                event.prevent_default()
                event.stop()

    current_model: reactive[str] = reactive("")  # 启动时从 config.json 实时读取
    project_name: reactive[str] = reactive("")
    project_dir: reactive[Path | None] = reactive(None)
    selected_chapter: reactive[int] = reactive(0)  # 0 表示未选章节
    selected_setting: reactive[str] = reactive("")  # "" 表示未选设定

    def __init__(self, root_dir: Path | None = None) -> None:
        super().__init__()
        self.root_dir = (root_dir or Path.cwd()).resolve()
        self.config = config_manager.load_config(self.root_dir)
        # 所有默认值从 config.json 实时读取，不硬编码
        self.current_model = self.config.effective_model()
        self.ai_client: AIClient | None = None
        self.engine: NovelEngine | None = None

    # ==================== Compose ====================

    def compose(self) -> ComposeResult:
        yield Static("", id="status-bar")
        with Horizontal(id="main-area"):
            with Vertical(id="sidebar-container"):
                yield SidebarTree()
            with Vertical(id="right-area"):
                with Vertical(id="right-top"):
                    yield PreviewPanel()
                with Vertical(id="right-bottom"):
                    yield LogPanel()
                    yield InputBar()
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_status()
        sidebar: SidebarTree = self.query_one(SidebarTree)
        sidebar.set_callbacks(
            on_select_chapter=self._on_select_chapter,
            on_select_setting=self._on_select_setting,
        )
        log: LogPanel = self.query_one(LogPanel)
        log.info("SuperWrites 启动。")
        # 配置自发现
        self.config, config_src = config_manager.auto_discover_config(self.root_dir)
        log.info(f"配置来源：{config_src}  base_url={self.config.effective_base_url()}")
        log.info(f"当前模型：{self.config.effective_model()}")
        masked = config_manager.mask_api_key(self.config.effective_api_key())
        if self.config.has_api_key():
            log.success(f"API Key 已就绪：{masked}")
        else:
            log.warning("未检测到 API Key，请配置")
            # 启动引导流程（异步）
            self.run_worker(self._first_run_setup(), exclusive=True)

    async def _first_run_setup(self) -> None:
        """首次启动引导：弹窗引导输入 Key。

        流程：
          1. 弹窗让用户输入 API Key（可跳过）
          2. 如果输入了，可选继续输入 base_url 和 model（默认 DeepSeek）
          3. 保存到 config.json
        """
        log = self.query_one(LogPanel)
        api_key = await self._ask_protected(StringPrompt(
            "请粘贴 DeepSeek（或 OpenAI 兼容服务）API Key",
            default="",
            title="首次启动 · 1/3 · API Key",
        ))
        if not api_key:
            log.warning("跳过 API Key 配置。AI 调用将不可用。")
            log.info("按 Ctrl+N 新建项目，或 /help 查看命令。")
            return

        base_url = await self._ask_protected(StringPrompt(
            "API base URL",
            default=self.config.base_url,
            title="首次启动 · 2/3 · base URL",
        )) or self.config.base_url

        model = await self._ask_protected(StringPrompt(
            "模型名",
            default=self.config.model,
            title="首次启动 · 3/3 · 模型",
        )) or self.config.model

        # 保存到 config.json
        self.config.api_key = api_key
        self.config.base_url = base_url or self.config.base_url
        self.config.model = model or self.config.model
        config_manager.save_config(self.config, self.root_dir)
        masked = config_manager.mask_api_key(self.config.effective_api_key())
        log.success(f"API Key 已保存到 config.json：{masked}")
        log.info("现在可以按 Ctrl+N 新建项目，或 /setup 启动规划。")

    # ==================== 状态栏 ====================

    def _refresh_status(self) -> None:
        bar = self.query_one("#status-bar", Static)
        if self.engine:
            meta = self.engine.meta
            total_words = meta.total_words()
            if meta.target_total_words > 0:
                pct = meta.progress_percent()
                bar_w = 10
                filled = int(bar_w * pct / 100) if pct else 0
                progress_bar = "█" * filled + "░" * (bar_w - filled)
                text = (
                    f" {meta.title or '未命名'}  "
                    f"· {meta.completed_chapters}/{meta.total_chapters} 章  "
                    f"· {total_words}/{meta.target_total_words} 字 [{progress_bar}] {pct:.0f}%  "
                    f"· {self.current_model}"
                )
            else:
                text = (
                    f" {meta.title or '未命名'}  "
                    f"· {meta.completed_chapters}/{meta.total_chapters} 章  "
                    f"· {total_words} 字  "
                    f"· {self.current_model}"
                )
        else:
            text = f" 未加载项目  · {self.current_model}"
        bar.update(text)

    # ==================== AI Client ====================

    async def _ensure_ai(self) -> AIClient:
        if self.ai_client is not None:
            return self.ai_client
        if not self.config.has_api_key():
            raise AIError("未配置 API Key。请设置环境变量 DEEPSEEK_API_KEY 或 config.json。")
        client = AIClient(
            api_key=self.config.effective_api_key(),
            base_url=self.config.effective_base_url(),
        )
        self.ai_client = client
        return client

    async def _ask(self, screen: ModalScreen) -> object:
        """从任意上下文安全弹出 Modal 并返回结果。"""
        future: asyncio.Future = asyncio.get_event_loop().create_future()

        async def _dialog():
            try:
                result = await self.push_screen_wait(screen)
            except Exception as e:
                if not future.done():
                    future.set_exception(e)
                return
            if not future.done():
                future.set_result(result)

        self.run_worker(_dialog(), exclusive=False, thread=False)
        result = await future
        ib = self.query_one(InputBar)
        ib.value = ""
        return result

    async def _ask_protected(self, screen: ModalScreen) -> object:
        """带异常保护的弹窗——任何异常捕获后通知用户并返回 None。"""
        try:
            return await self._ask(screen)
        except Exception as e:
            self.query_one(LogPanel).error(f"弹窗异常: {e}")
            return None

    async def _prompt_retry(self, action_name: str, error_msg: str) -> bool:
        """弹窗询问用户是否重试。返回 True=重试，False=放弃。"""
        pick = await self._ask_protected(ProjectPicker(
            f"  {action_name} 失败: {error_msg[:120]}",
            ["重试", "跳过并继续"],
            title=f"错误 · {action_name}",
        ))
        return pick is not None and pick.startswith("重试")

    async def _ai_with_retry(self, action_name: str, coro_factory):
        """AI 调用自动重试。失败时弹窗询问用户。

        参数:
            action_name: 动作名称（如"生成故事方向"）
            coro_factory: 返回 awaitable 的工厂函数，每次重试重新调用

        返回: 成功结果，或 None（用户放弃）
        """
        log = self.query_one(LogPanel)
        max_n = self.config.max_retries
        for i in range(max_n):
            try:
                return await coro_factory()
            except Exception as e:
                msg = str(e) or type(e).__name__
                log.error(f"{action_name} 失败 ({i+1}/{max_n}): {msg}")
                if i < max_n - 1:
                    retry = await self._prompt_retry(action_name, msg)
                    if not retry:
                        return None
                else:
                    log.error(f"{action_name} 已达最大重试次数 {max_n}")
                    return None
        return None

    async def _ask_choices(
        self, title: str, label: str, items: list[str], max_attempts: int = 5
    ) -> str | None:
        """带序号边界检查的选择弹窗。

        行为:
          1. 弹出弹窗，让用户输入 1-N 的序号（也可直接输入自定义内容）
          2. 若输入的数字超出 [1, N]，自动重新弹出，提示原因
          3. 超过 max_attempts 次错误后返回 None
        """
        log = self.query_one(LogPanel)
        n = len(items)
        last_error = ""
        for attempt in range(1, max_attempts + 1):
            full_label = label
            if last_error:
                full_label = f"{label}\n!! {last_error} !!"
            picker = ProjectPicker(
                full_label, items, title=f"{title} [{attempt}/{max_attempts}]"
            )
            val = await self._ask_protected(picker)
            if val is None:
                return None
            val = val.strip()
            if not val:
                last_error = "输入为空，请输入 1-%d 的序号或自定义内容" % n
                continue
            if val.isdigit():
                idx = int(val)
                if 1 <= idx <= n:
                    return items[idx - 1].strip()
                last_error = "序号 %d 超出范围 1-%d，请重新输入" % (idx, n)
                log.warning(last_error)
                continue
            return val
        log.error(f"已超过最大尝试次数 {max_attempts}，已取消")
        return None

    def _spawn(self, coro) -> None:
        """从非 async 上下文启动一个协程，自动捕获未处理异常。"""
        async def _guarded():
            try:
                await coro
            except Exception as e:
                try:
                    self.query_one(LogPanel).error(f"内部错误: {e}")
                except Exception:
                    pass
        self.run_worker(_guarded(), exclusive=False, thread=False)

    async def on_unmount(self) -> None:
        if self.ai_client is not None:
            await self.ai_client.close()

    # ==================== 选择事件 ====================

    def _on_select_chapter(self, number: int) -> None:
        self.selected_chapter = number
        self.selected_setting = ""
        if self.engine and self.project_dir:
            preview = self.query_one(PreviewPanel)
            preview.show_chapter(number, self.engine.meta, self.project_dir)

    def _on_select_setting(self, key: str) -> None:
        self.selected_setting = key
        self.selected_chapter = 0
        if self.project_dir:
            preview = self.query_one(PreviewPanel)
            preview.show_setting(key, self.project_dir)

    # ==================== 快捷键行为 ====================

    def action_focus_input(self) -> None:
        self.query_one(InputBar).focus()

    def action_save_all(self) -> None:
        if self.engine and self.project_dir:
            save_meta(self.project_dir, self.engine.meta)
            self.query_one(LogPanel).success("已保存 metadata.json")
        else:
            self.query_one(LogPanel).warning("无活动项目可保存")

    def action_export(self) -> None:
        self._spawn(self._action_export_impl())

    async def _action_export_impl(self) -> None:
        log = self.query_one(LogPanel)
        if not self.engine or not self.project_dir:
            log.warning("无活动项目可导出")
            return
        # 1. 选择格式
        fmt = await self._ask_protected(ProjectPicker(
            "选择导出格式", ["TXT", "EPUB", "TXT + EPUB"],
            title="导出 · 1/2",
        ))
        if not fmt:
            return
        # 2. 选择目录
        default_dir = str(self.project_dir.parent / "exports")
        directory = await self._ask_protected(StringPrompt(
            f"导出目录（默认 {default_dir}）",
            default=default_dir,
            title="导出 · 2/2",
        ))
        if not directory:
            return
        # 展开 ~ 路径
        directory = os.path.expanduser(directory)
        out_dir = Path(directory)
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            log.error(f"无法创建目录 {out_dir}: {e}")
            return
        try:
            if "TXT" in fmt:
                path = await asyncio.to_thread(
                    export_txt, self.project_dir, self.engine.meta, out_dir
                )
                log.success(f"TXT 导出完成：{path}")
            if "EPUB" in fmt:
                path = await asyncio.to_thread(
                    export_epub, self.project_dir, self.engine.meta, out_dir
                )
                log.success(f"EPUB 导出完成：{path}")
        except Exception as e:
            log.error(f"导出失败：{e}")

    def action_new_project(self) -> None:
        self._spawn(self._action_new_project_impl())

    async def _action_new_project_impl(self) -> None:
        log = self.query_one(LogPanel)
        name = await self._ask_protected(StringPrompt(
            "项目名（字母/数字/中文，将作为子目录名）",
            default="my_novel",
            title="新建项目 · 1/3",
        ))
        if not name:
            return
        name = re.sub(r"[^\w一-鿿\-]", "_", name).strip("_") or "untitled"
        path = project_path(self.root_dir, name)
        if path.exists():
            log.warning(f"项目已存在：{name}")
            return
        chapters_n = await self._ask_protected(IntPrompt(
            "总章节数", default=30, min_v=1, max_v=999,
            title="新建项目 · 2/3",
        ))
        if not chapters_n:
            return
        total_words_str = await self._ask_protected(StringPrompt(
            f"目标总字数（当前默认 {self.config.default_target_total_words}，输入 0 表示不设定）",
            default=str(self.config.default_target_total_words),
            title="新建项目 · 3/3",
        ))
        try:
            target_words = int(re.sub(r"[^\d]", "", total_words_str or "0") or "0")
            target_words = max(0, min(10_000_000, target_words))
        except ValueError:
            target_words = self.config.default_target_total_words

        init_project(self.root_dir, name, total_chapters=chapters_n,
                     target_total_words=target_words)
        await self._load_project(name)
        if target_words > 0:
            per_chapter = target_words // chapters_n
            log.success(
                f"项目已创建：{name}（{chapters_n} 章，目标 {target_words} 字，"
                f"每章约 {per_chapter} 字）"
            )
        else:
            log.success(f"项目已创建：{name}（{chapters_n} 章）")

    def action_open_project(self) -> None:
        self._spawn(self._action_open_project_impl())

    async def _action_open_project_impl(self) -> None:
        projects = config_manager.list_projects(self.root_dir / "novels")
        if not projects:
            self.query_one(LogPanel).warning("novels/ 下没有任何项目")
            return
        names = [p["name"] for p in projects]
        log = self.query_one(LogPanel)
        log.info("可用项目：")
        for i, p in enumerate(projects, 1):
            log.info(f"  [{i}] {p['title']} ({p['completed']}/{p['chapters']})")
        name = await self._ask_protected(ProjectPicker(
            "输入项目编号", names, title="打开项目",
        ))
        if not name:
            return
        await self._load_project(name)

    # ==================== 输入框提交 ====================

    async def on_input_submitted(self, event) -> None:
        """主输入框（InputBar）的提交事件。

        弹窗（StringPrompt/IntPrompt/ProjectPicker）有自己的 on_input_submitted，
        不应触发这里——Textual 的事件冒泡有时会让多个监听器都收到。
        通过 `event.input` 是不是 InputBar 来严格区分。
        """
        # 弹窗或命令面板打开时：完全忽略
        if any(isinstance(s, ModalScreen) for s in self.screen_stack):
            return
        # 事件来源必须是自己（不是弹窗里冒泡上来的）
        if not isinstance(event.input, InputBar):
            return

        text = event.input.value.strip()
        event.input.value = ""
        if not text:
            return
        m = _CMD_RE.match(text)
        if not m:
            return
        cmd = m.group(1).lower()
        args = (m.group(2) or "").strip()

        if cmd == "help":
            self._show_help()
        elif cmd == "setup":
            self._spawn(self._cmd_setup())
        elif cmd == "write":
            self._spawn(self._cmd_write(args))
        elif cmd == "export":
            self._spawn(self._cmd_export(args))
        elif cmd == "model":
            self._spawn(self._cmd_model(args))
        # 不认识的命令：静默忽略（不弹警告，避免干扰）

    def _show_help(self) -> None:
        log = self.query_one(LogPanel)
        for line in [
            "setup                        启动规划向导（10 步流程前 6 步）",
            "write N                      生成第 N 章",
            "write N M                    生成第 N 章，目标 M 字",
            "write N-M                    批量生成第 N 到 M 章",
            "write N-M K                  批量生成 N-M 章，每章 K 字",
            "export                       弹窗选择导出格式",
            "export txt|epub              直接导出指定格式",
            "model <name>                 切换模型",
            "help                         显示本帮助",
        ]:
            log.info(line)

    # ==================== 命令实现 ====================

    async def _cmd_setup(self) -> None:
        log = self.query_one(LogPanel)
        if not self.engine:
            log.warning("请先新建或打开项目（Ctrl+N / Ctrl+O）")
            return
        try:
            await self._ensure_ai()
        except AIError as e:
            log.error(str(e))
            return

        # 1. 偏好
        log.info("── 步骤 1/7：收集偏好 ──")
        notes = await self._ask_protected(StringPrompt(
            "题材/文风/字数/特殊要求（一句话即可）",
            default=self.engine.meta.writing_notes or "",
            title="规划 · 1/7",
        ))
        if not notes:
            return
        self.engine.collect_preferences(notes)

        # 1.5 每章目标字数
        log.info("── 步骤 1.5/7：设置每章目标字数 ──")
        # 默认值优先级：已有章节设定 > 总字数/章节数 > config 兜底
        default = self.config.default_words_per_chapter
        if self.engine.meta.target_total_words > 0 and self.engine.meta.total_chapters > 0:
            default = self.engine.meta.target_total_words // self.engine.meta.total_chapters
        words_str = await self._ask_protected(StringPrompt(
            "每章目标字数（默认 %d）" % default,
            default=str(default),
            title="规划 · 1.5/7",
        ))
        if words_str:
            try:
                words_n = int(re.sub(r"\D", "", words_str) or str(self.config.default_words_per_chapter))
                words_n = max(500, min(20000, words_n))
                for ch in self.engine.meta.chapters.values():
                    ch.target_words = words_n
                self.engine.meta.writing_notes = (
                    f"{self.engine.meta.writing_notes or ''}\n（每章目标字数：{words_n}）"
                ).strip()
                self.engine._save_meta()
                log.success(f"已设置每章目标字数：{words_n}")
            except ValueError:
                log.warning("字数解析失败，使用默认值")

        # 2. 方向
        log.info("── 步骤 2/7：生成故事方向 ──")
        log.info("调用 AI 中，请稍候…")
        dir_items = await self._ai_with_retry(
            "生成故事方向",
            lambda: self.engine.generate_directions(count=self.config.directions_count)
        )
        if not dir_items:
            log.warning("故事方向生成已取消")
            return
        direction = await self._ask_choices(
            title=f"规划 · 2/7 · 选择故事方向（共 {len(dir_items)} 个）",
            label="选择方向序号（1-%d），或直接输入自定义方向：" % len(dir_items),
            items=dir_items,
        )
        if not direction:
            return
        self.engine.select_direction(direction)

        # 3. 书名
        log.info("── 步骤 3/7：生成书名 ──")
        title_items = await self._ai_with_retry(
            "生成书名",
            lambda: self.engine.generate_titles(count=self.config.titles_count)
        )
        if not title_items:
            log.warning("书名生成已取消")
            return
        title = await self._ask_choices(
            title="规划 · 3/7",
            label="选择书名序号，或直接输入自定义书名：",
            items=title_items,
        )
        if not title:
            return
        self.engine.select_title(title)
        self.current_model = self.config.effective_model()
        self.engine.meta.current_model = self.current_model
        self.engine.meta.base_url = self.config.effective_base_url()
        self._refresh_status()

        # 4. 角色
        log.info("── 步骤 4/7：生成角色设定 ──")
        chars = await self._ai_with_retry(
            "生成角色设定",
            lambda: self.engine.generate_characters()
        )
        log.success("角色设定已生成并保存") if chars else log.warning("角色设定生成已取消")

        # 5. 世界观
        log.info("── 步骤 5/7：生成世界观 ──")
        world = await self._ai_with_retry(
            "生成世界观",
            lambda: self.engine.generate_world()
        )
        log.success("世界观已生成并保存") if world else log.warning("世界观生成已取消")

        # 6. 大纲
        log.info("── 步骤 6/7：生成大纲 ──")
        outline = await self._ai_with_retry(
            "生成大纲",
            lambda: self.engine.generate_outline()
        )
        log.success(f"大纲已生成（{len(self.engine.meta.chapters)} 章）") if outline else log.warning("大纲生成已取消")

        # 7. 规范
        log.info("── 步骤 7/7：固化写作规范 ──")
        norm = await self._ai_with_retry(
            "生成写作规范",
            lambda: self.engine.generate_writing_norm()
        )
        log.success("写作规范已生成并保存") if norm else log.warning("写作规范生成已取消")

        self._reload_sidebar()
        preview = self.query_one(PreviewPanel)
        preview.show_setting("norm", self.project_dir)
        log.success("规划完成！可用 /write 1 开始生成章节，或在左侧选中章节按回车。")

    async def _cmd_write(self, args: str) -> None:
        log = self.query_one(LogPanel)
        if not self.engine:
            log.warning("请先加载项目")
            return
        if not args:
            log.warning("用法：write N / write N M / write N-M / write N-M K")
            return
        tokens = args.split()
        try:
            if len(tokens) == 1:
                t = tokens[0]
                if "-" in t:
                    # "N-M" 单 token 范围
                    start_s, end_s = t.split("-", 1)
                    start, end = int(start_s), int(end_s)
                    for n in range(start, end + 1):
                        await self._generate_chapter(n)
                else:
                    n = int(t)
                    await self._generate_chapter(n)
            elif len(tokens) == 2:
                a, b = tokens[0], tokens[1]
                if "-" in a:
                    start_s, end_s = a.split("-", 1)
                    start, end = int(start_s), int(end_s)
                    words = int(b)
                    for n in range(start, end + 1):
                        await self._generate_chapter(n, target_words=words)
                else:
                    await self._generate_chapter(int(a), target_words=int(b))
            else:
                log.error(f"参数错误：{args}")
        except ValueError:
            log.error(f"参数错误：{args}")

    async def _cmd_export(self, args: str) -> None:
        log = self.query_one(LogPanel)
        if not self.engine or not self.project_dir:
            log.warning("无活动项目可导出")
            return
        fmt = (args or "").lower()
        if fmt not in ("txt", "epub", ""):
            log.warning(f"不支持的格式：{fmt}（支持 txt / epub / 留空弹窗选择）")
            return
        try:
            if fmt == "txt":
                path = await asyncio.to_thread(export_txt, self.project_dir, self.engine.meta)
                log.success(f"TXT 导出完成：{path}")
            elif fmt == "epub":
                path = await asyncio.to_thread(export_epub, self.project_dir, self.engine.meta)
                log.success(f"EPUB 导出完成：{path}")
            else:
                # 无参数时弹窗选择
                pick = await self._ask_protected(ProjectPicker(
                    "选择导出格式", ["TXT", "EPUB", "TXT + EPUB"],
                    title="导出",
                ))
                if not pick:
                    return
                if "TXT" in pick:
                    path = await asyncio.to_thread(export_txt, self.project_dir, self.engine.meta)
                    log.success(f"TXT 导出完成：{path}")
                if "EPUB" in pick:
                    path = await asyncio.to_thread(export_epub, self.project_dir, self.engine.meta)
                    log.success(f"EPUB 导出完成：{path}")
        except Exception as e:
            log.error(f"导出失败：{e}")

    async def _cmd_model(self, args: str) -> None:
        log = self.query_one(LogPanel)
        if not args:
            log.info(f"当前模型：{self.current_model}")
            return
        self.current_model = args
        if self.engine and self.project_dir:
            self.engine.meta.current_model = args
            save_meta(self.project_dir, self.engine.meta)
        self._refresh_status()
        log.success(f"已切换模型：{args}")

    # ==================== 章节生成流程 ====================

    async def _generate_chapter(self, number: int, target_words: int | None = None) -> None:
        log = self.query_one(LogPanel)
        if not self.engine or not self.project_dir:
            log.warning("无活动项目")
            return
        # 前置步骤检查：未完成 /setup 则禁止生成
        meta = self.engine.meta
        if not meta.direction:
            log.warning("尚未选择故事方向，请先运行 /setup")
            return
        if not meta.title:
            log.warning("尚未选择书名，请先运行 /setup")
            return
        # 必须存在章节大纲
        if not meta.chapters:
            log.warning("尚未生成大纲，请先运行 /setup")
            return
        ch = meta.chapters.get(str(number))
        if not ch:
            log.warning(f"第 {number} 章不在大纲中（大纲共 {len(meta.chapters)} 章）")
            return
        # 必须存在世界/角色/规范
        from .core.novel_engine import read_text
        if not read_text(self.project_dir, "world").strip():
            log.warning("世界观为空，请先运行 /setup 完成规划")
            return
        if not read_text(self.project_dir, "characters").strip():
            log.warning("角色设定为空，请先运行 /setup 完成规划")
            return
        if not read_text(self.project_dir, "norm").strip():
            log.warning("写作规范为空，请先运行 /setup 完成规划")
            return
        try:
            await self._ensure_ai()
        except AIError as e:
            log.error(str(e))
            return

        log.info(f"开始生成第 {number} 章（目标 {target_words or '默认'} 字）…")
        sidebar = self.query_one(SidebarTree)
        sidebar.update_chapter_status(number, CHAPTER_STATUS_WRITING)

        preview = self.query_one(PreviewPanel)
        is_selected = self.selected_chapter == number

        if is_selected:
            preview.stream_begin(number, self.engine.meta, self.project_dir)

        async def on_token(chunk: str) -> None:
            if is_selected:
                preview.stream_append(chunk)

        content = await self._ai_with_retry(
            f"生成第 {number} 章",
            lambda: self.engine.write_chapter(number, on_token=on_token)
        )
        if not content:
            log.warning(f"第 {number} 章生成已取消")
            sidebar.update_chapter_status(number, CHAPTER_STATUS_PENDING)
            return
        log.success(f"第 {number} 章生成完成，字数：{len(content)}")

        sidebar.update_chapter_status(number, CHAPTER_STATUS_DONE)
        self._refresh_status()
        # 生成完成后刷新预览（用完整格式替换流式碎片）
        if is_selected:
            preview.stream_done(number, self.engine.meta, self.project_dir)
        # 异步后处理（摘要+自检）不阻塞 UI
        asyncio.create_task(self._do_summarize(number))
        asyncio.create_task(self._do_self_check(number))

    # ==================== Sidebar 消息处理 ====================

    def on_sidebar_tree_chapter_status_changed(
        self, event: SidebarTree.ChapterStatusChanged
    ) -> None:
        """章节状态变更后，如果当前预览的就是该章节，则刷新预览面板。"""
        if self.selected_chapter == event.number and self.engine and self.project_dir:
            try:
                preview = self.query_one(PreviewPanel)
                preview.show_chapter(event.number, self.engine.meta, self.project_dir)
            except Exception:
                pass

    async def _do_summarize(self, number: int) -> None:
        if self.ai_client is None:
            return
        log = self.query_one(LogPanel)
        try:
            summary = await self.engine.summarize_chapter(number)
            if summary:
                log.success(f"第 {number} 章摘要已更新（{len(summary)} 字）")
        except Exception as e:
            msg = str(e)
            if "closed" not in msg.lower() and "cancel" not in msg.lower():
                log.warning(f"摘要失败：{msg[:80]}")

    async def _do_self_check(self, number: int) -> None:
        if self.ai_client is None:
            return
        log = self.query_one(LogPanel)
        try:
            result = await self.engine.self_check_chapter(
                number, threshold=self.config.self_check_threshold
            )
        except Exception as e:
            log.warning(f"自检调用失败: {str(e)[:80]}")
            return
        score = result["score"]
        if result["passed"]:
            log.info(f"第 {number} 章自检通过（{score:.2f}）")
            return
        issues = result["issues"]
        log.warning(f"第 {number} 章自检未通过（{score:.2f}）：{'；'.join(issues[:3])}")
        if self.selected_chapter == number:
            self.query_one(PreviewPanel).show_conflict(issues, score, number)
        # 弹窗询问是否重生成
        choice = await self._ask_protected(ProjectPicker(
            "自检未通过（%.2f），是否根据问题重生成？" % score,
            ["是，根据问题修正并重新生成", "否，保留当前版本（评分 %.2f）" % score],
            title="自检 · 第%d章 · %.2f" % (number, score),
        ))
        if not choice or not choice.startswith("是"):
            return
        log.info(f"正在根据自检问题重生成第 {number} 章…")
        sidebar = self.query_one(SidebarTree)
        sidebar.update_chapter_status(number, CHAPTER_STATUS_WRITING)
        ch_meta = self.engine.meta.chapters[str(number)]
        content = await self._ai_with_retry(
            f"重生成第 {number} 章",
            lambda: self.engine.write_chapter(
                number,
                target_words=ch_meta.target_words or None,
                extra_constraints="\n".join(issues),
            ),
        )
        if not content:
            log.warning(f"第 {number} 章重生成已取消")
            sidebar.update_chapter_status(number, CHAPTER_STATUS_DONE)
            return
        log.success(f"第 {number} 章重新生成完成，字数：{len(content)}")
        sidebar.update_chapter_status(number, CHAPTER_STATUS_DONE)
        self._refresh_status()
        if self.selected_chapter == number:
            self.query_one(PreviewPanel).show_chapter(number, self.engine.meta, self.project_dir)
        asyncio.create_task(self._do_self_check(number))

    # ==================== 项目加载 ====================

    async def _load_project(self, name: str) -> None:
        path = project_path(self.root_dir, name)
        if not (path / "metadata.json").exists():
            self.query_one(LogPanel).warning(f"项目不存在：{name}")
            return
        meta = load_meta(path)
        self.project_dir = path
        self.project_name = name
        if self.ai_client is None:
            try:
                await self._ensure_ai()
            except AIError:
                # 无 AI 也允许看 metadata
                pass
        # 如果没有 AI client，给一个占位（不会主动调用）
        from .core.ai_client import AIClient as _AIClient

        class _NoOpClient(_AIClient):
            """占位 AI client：仅用于构造 NovelEngine，不真正调用。"""

            def __init__(self):
                pass  # 跳过父类构造

            async def stream_with_fallback(self, req):  # pragma: no cover
                raise AIError("AI client 未配置（仅用于查看项目）")

            async def chat(self, req):  # pragma: no cover
                raise AIError("AI client 未配置")

            async def close(self):  # pragma: no cover
                pass

        ai = self.ai_client or _NoOpClient()
        self.engine = NovelEngine(ai, path, model=self.config.effective_model())
        self.current_model = meta.current_model or self.config.effective_model()
        self._refresh_status()
        self._reload_sidebar()
        self.query_one(LogPanel).success(f"已加载项目：{name}")

    def _reload_sidebar(self) -> None:
        if not self.engine:
            return
        sidebar = self.query_one(SidebarTree)
        sidebar.refresh_from_meta(self.engine.meta)
        sidebar.set_meta_cache(self.engine.meta)


def main() -> None:
    """CLI 入口。"""
    app = SuperWritesApp()
    app.run()


if __name__ == "__main__":
    main()