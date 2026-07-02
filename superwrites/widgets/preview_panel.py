"""右上详情/预览面板。流式按行刷新，不闪屏。"""

from __future__ import annotations

import time
from pathlib import Path

from textual.widgets import RichLog, Static
from textual.containers import Vertical

from ..core.novel_engine import (
    CHAPTER_STATUS_DONE,
    CHAPTER_STATUS_PENDING,
    CHAPTER_STATUS_WRITING,
    NovelMeta,
    clean_markdown,
    parse_outline,
    read_text,
    load_chapter,
)


class PreviewPanel(Vertical):
    """右上预览面板。

    流式输出策略：
      - 累积 chunk 到 buffer
      - 每 80ms 刷新一次：把 buffer 按 \\n 拆分，把完整行 write 出去
      - 换行符保留在 buffer 末尾，确保视觉对齐
      - 永不 clear+rewrite（避免闪屏）
    """

    DEFAULT_CSS = """
    PreviewPanel {
        width: 100%;
        height: 100%;
    }
    PreviewPanel > #preview-title {
        height: 1;
        padding: 0 1;
        text-style: bold;
        color: $foreground;
    }
    PreviewPanel > #preview-body {
        width: 100%;
        height: 1fr;
        border: none;
        scrollbar-size-vertical: 0;
        scrollbar-size-horizontal: 0;
    }
    """

    FLUSH_INTERVAL = 0.08  # 秒

    def __init__(self) -> None:
        super().__init__(id="preview-panel")
        self._streaming = False
        self._stream_buf = ""
        self._stream_header = ""
        self._stream_written_chars = 0  # 已写入 RichLog 的字符数（不包含 header）
        self._last_flush = 0.0
        self._title_widget: Static | None = None
        self._body_widget: RichLog | None = None

    def compose(self):
        self._title_widget = Static("", id="preview-title")
        self._body_widget = RichLog(
            id="preview-body",
            highlight=False,
            markup=True,
            wrap=True,
            auto_scroll=True,
            max_lines=0,
        )
        yield self._title_widget
        yield self._body_widget

    def on_mount(self) -> None:
        self._set_title("在左侧选择项目")

    # ---- 静态展示 ----

    def show_setting(self, key: str, project_dir: Path) -> None:
        title_map = {"world": "世界观", "characters": "角色设定", "norm": "写作规范"}
        self._set_title(title_map.get(key, key))
        content = read_text(project_dir, key)
        self._body_widget.clear()
        if content:
            self._body_widget.write(clean_markdown(content))
        else:
            self._body_widget.write("[dim]（暂无内容）[/]")

    def show_chapter(self, number: int, meta: NovelMeta, project_dir: Path) -> None:
        self._streaming = False
        self._stream_buf = ""
        ch = meta.chapters.get(str(number))
        if ch is None:
            self._set_title(f"第{number}章")
            self._body_widget.clear()
            self._body_widget.write("[dim]（未在大纲中找到此章节）[/]")
            return
        if ch.target_words > 0:
            target = ch.target_words
        elif meta.target_total_words > 0 and meta.total_chapters > 0:
            target = meta.target_total_words // meta.total_chapters
        else:
            target = 0
        self._set_title(f"第{ch.number}章  {ch.title}")
        out = []
        status_txt = {
            CHAPTER_STATUS_PENDING: "[ ] 待生成",
            CHAPTER_STATUS_WRITING: "[▶] 生成中",
            CHAPTER_STATUS_DONE: "[✓] 已完成",
        }.get(ch.status, ch.status)
        out.append(status_txt)
        if target > 0 and ch.status == CHAPTER_STATUS_DONE:
            ratio = ch.word_count / max(target, 1)
            bw = 12
            filled = int(bw * min(ratio, 1.0))
            bar = "█" * filled + "░" * (bw - filled)
            c = "green" if ratio >= 0.85 else "yellow" if ratio >= 0.6 else "red"
            out.append(f"字数  {ch.word_count} / {target}  [{c}]{bar}[/] {ratio*100:.0f}%")
        elif target > 0:
            out.append(f"字数  {ch.word_count} / 目标 {target}")
        else:
            out.append(f"字数  {ch.word_count}")
        if ch.summary:
            out.append(f"摘要  {ch.summary}")
        if ch.self_check_score >= 0:
            c = "green" if ch.self_check_score >= 0.7 else "yellow" if ch.self_check_score >= 0.5 else "red"
            out.append(f"自检  [{c}]{ch.self_check_score:.2f}[/]")
            if ch.self_check_note:
                out.append(f"     [dim]{ch.self_check_note}[/]")
        out.append("")
        out.append("[dim]── 大纲 ──[/]")
        otext = read_text(project_dir, "outline")
        matched = None
        for item in parse_outline(otext):
            if item["number"] == number:
                matched = item
                break
        if matched:
            if matched["title"]:
                out.append(matched["title"])
            if matched["core_event"]:
                out.append(matched["core_event"])
        else:
            out.append("[dim]（大纲中未找到此章）[/]")
        out.append("")
        out.append("[dim]── 正文 ──[/]")
        full = load_chapter(project_dir, number)
        out.append(full if full else "[dim]（尚未生成正文）[/]")
        self._body_widget.clear()
        self._body_widget.write("\n".join(out))

    # ---- 流式输出 ----

    def stream_begin(self, number: int, meta: NovelMeta, project_dir: Path) -> None:
        ch = meta.chapters.get(str(number))
        if ch is None:
            return
        self._streaming = True
        self._stream_buf = ""
        self._stream_written_chars = 0
        self._last_flush = time.monotonic()
        target = ch.target_words or (
            meta.target_total_words // meta.total_chapters
            if meta.target_total_words > 0 and meta.total_chapters > 0
            else 0
        )
        self._set_title(f"第{ch.number}章  {ch.title}  [▶] 生成中…")
        otext = read_text(project_dir, "outline")
        matched = None
        for item in parse_outline(otext):
            if item["number"] == number:
                matched = item
                break
        core = (matched["title"] + "——" + matched["core_event"]) if matched and matched.get("title") else ""
        core_line = f"[dim]大纲: {core[:80]}[/]\n" if core else ""
        self._stream_header = (
            f"目标 {target} 字\n"
            + core_line
            + "[dim]── 正文 ──[/]\n"
        )
        self._body_widget.clear()
        self._body_widget.write(self._stream_header)

    def stream_append(self, chunk: str) -> None:
        """累积 chunk，按行刷新。永不 clear。"""
        if not self._streaming:
            return
        self._stream_buf += chunk
        # 节流刷新
        now = time.monotonic()
        if now - self._last_flush < self.FLUSH_INTERVAL:
            return
        self._last_flush = now
        self._flush_lines()

    def _flush_lines(self) -> None:
        """把 buffer 中已完成的行（不含末尾未完成行）写入 RichLog。"""
        if not self._stream_buf:
            return
        # 找到最后一个换行符位置
        nl = self._stream_buf.rfind("\n")
        if nl == -1:
            return  # 还没攒到一行
        to_write = self._stream_buf[:nl]  # 完整行（含 \n）
        self._stream_buf = self._stream_buf[nl + 1 :]
        # 一次性写入所有完整行
        self._body_widget.write(to_write)

    def stream_done(self, number: int, meta: NovelMeta, project_dir: Path) -> None:
        # 先 flush 残余内容
        if self._stream_buf:
            self._body_widget.write(self._stream_buf)
            self._stream_buf = ""
        self._streaming = False
        self.show_chapter(number, meta, project_dir)

    def show_conflict(self, issues: list[str], score: float, chapter_number: int) -> None:
        self._set_title(f"自检冲突 · 第{chapter_number}章 · {score:.2f}")
        self._body_widget.clear()
        self._body_widget.write(f"[bold red]自检未通过 · 评分 {score:.2f}[/]\n")
        for i, iss in enumerate(issues, 1):
            self._body_widget.write(f"  [yellow]{i}. {iss}[/]")
        self._body_widget.write("\n[dim]按 y 重新生成，按 n 忽略[/]")

    def _set_title(self, text: str) -> None:
        self._title_widget.update(text)