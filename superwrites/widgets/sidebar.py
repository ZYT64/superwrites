"""左侧大纲树控件。

显示故事设定（世界观/角色/写作规范）和章节列表，章节旁标注状态。
"""

from __future__ import annotations

from typing import Any

from textual.widgets import Tree
from textual.widgets.tree import TreeNode

from ..core.novel_engine import (
    CHAPTER_STATUS_DONE,
    CHAPTER_STATUS_PENDING,
    CHAPTER_STATUS_WRITING,
    ChapterMeta,
    NovelMeta,
)


_STATUS_LABEL = {
    CHAPTER_STATUS_PENDING: "[ ]",
    CHAPTER_STATUS_WRITING: "[▶]",
    CHAPTER_STATUS_DONE: "[✓]",
}


class SidebarTree(Tree[Any]):
    """左侧大纲树。

    节点数据约定：
      - 设定类节点 data = {"kind": "setting", "key": "world"|"characters"|"norm"}
      - 章节节点 data = {"kind": "chapter", "number": N}
    """

    DEFAULT_CSS = """
    SidebarTree {
        width: 100%;
        height: 100%;
        padding: 0 1;
        scrollbar-size-vertical: 0;
        scrollbar-size-horizontal: 0;
    }
    """

    def __init__(self) -> None:
        super().__init__("故事设定", id="sidebar-tree")
        self.show_root = True
        self._on_select_chapter = None
        self._on_select_setting = None

    def set_callbacks(self, on_select_chapter, on_select_setting) -> None:
        """注入选中回调。"""
        self._on_select_chapter = on_select_chapter
        self._on_select_setting = on_select_setting

    # ---- 渲染 ----

    def refresh_from_meta(self, meta: NovelMeta) -> None:
        """根据元数据重绘整棵树。"""
        self.clear()
        # 根
        self.root.set_label(meta.title or "未命名项目")

        # 设定类节点
        settings = self.root.add("故事设定", expand=True)
        settings.add_leaf("世界观", data={"kind": "setting", "key": "world"})
        settings.add_leaf("角色设定", data={"kind": "setting", "key": "characters"})
        settings.add_leaf("写作规范", data={"kind": "setting", "key": "norm"})

        # 大纲节点
        if meta.chapters:
            ch_node = self.root.add(
                f"章节 {meta.completed_chapters}/{meta.total_chapters}",
                expand=True,
            )
            for key in sorted(meta.chapters.keys(), key=lambda x: int(x)):
                ch: ChapterMeta = meta.chapters[key]
                label = f"{_STATUS_LABEL.get(ch.status, '[?]')} {ch.number:>2}. {ch.title}"
                node = ch_node.add_leaf(label, data={"kind": "chapter", "number": ch.number})
                if ch.status == CHAPTER_STATUS_WRITING:
                    node.allow_expand = False
        else:
            self.root.add_leaf("章节列表（暂无）")

        self.root.expand_all()

    def update_chapter_status(self, number: int, status: str) -> None:
        """增量更新单个章节的状态图标（避免整树重绘）。

        触发一个自定义消息 `ChapterStatusChanged`，由 App 处理：
        - 刷新 PreviewPanel（如果当前选中的就是这个章节）
        """
        target_data = {"kind": "chapter", "number": number}
        node = self._find_node(self.root, target_data)
        if node is None:
            return
        # 从外部 meta 缓存取标题（由 App 通过 set_meta_cache 注入）
        title = self._title_cache.get(number, f"第{number}章")
        from ..core.novel_engine import (
            CHAPTER_STATUS_DONE,
            CHAPTER_STATUS_PENDING,
            CHAPTER_STATUS_WRITING,
        )
        status_label = {
            CHAPTER_STATUS_PENDING: "[ ]",
            CHAPTER_STATUS_WRITING: "[▶]",
            CHAPTER_STATUS_DONE: "[✓]",
        }.get(status, "[?]")
        node.set_label(f"{status_label} 第{number}章 {title}")
        # 通知 App 同步刷新预览面板
        self.post_message(self.ChapterStatusChanged(number, status))

    from textual.message import Message

    class ChapterStatusChanged(Message):
        """章节状态变更消息。"""

        def __init__(self, number: int, status: str) -> None:
            super().__init__()
            self.number = number
            self.status = status

    def set_meta_cache(self, meta: NovelMeta) -> None:
        """注入章节标题缓存（用于 update_chapter_status 时显示标题）。"""
        self._title_cache = {
            int(k): v.title for k, v in meta.chapters.items()
        }

    def on_mount(self) -> None:
        self._title_cache = {}

    # ---- 内部 ----

    @staticmethod
    def _meta_for(node: TreeNode) -> dict[str, Any] | None:
        d = node.data
        return d if isinstance(d, dict) else None

    @staticmethod
    def _find_node(node: TreeNode, target: dict[str, Any]) -> TreeNode | None:
        """深度优先查找 data 完全匹配的节点。"""
        if node.data == target:
            return node
        for child in node.children:
            found = SidebarTree._find_node(child, target)
            if found is not None:
                return found
        return None

    # ---- 事件 ----

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        node = event.node
        data = node.data
        if not isinstance(data, dict):
            return
        if data.get("kind") == "chapter" and self._on_select_chapter:
            self._on_select_chapter(int(data["number"]))
        elif data.get("kind") == "setting" and self._on_select_setting:
            self._on_select_setting(str(data["key"]))