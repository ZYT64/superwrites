"""小说生成引擎：实现 V2 版的 10 步流程 + 元数据管理 + 摘要 + 自检。

10 步流程（与需求文档一致）：
  1. 收集用户偏好（题材、文风、字数等）
  2. 生成 3 个故事方向，让用户选择
  3. 根据选择的方向生成书名候选
  4. 生成角色设定
  5. 生成世界观
  6. 生成大纲（按章节列出核心事件）
  7. 固化写作规范（综合所有信息生成"宪法"）
  8. 按章节生成正文
  9. 生成后提炼 150 字摘要
 10. 自检：AI 评判章节是否偏离人设/规范

存储约定：
  novels/<项目名>/
  ├── metadata.json        # 项目级元数据（章节状态、字数、当前模型、摘要）
  ├── 世界观.txt           # 步骤 5
  ├── 角色设定.txt         # 步骤 4
  ├── 大纲.txt             # 步骤 6
  ├── 写作规范.txt         # 步骤 7（每次生成章节都作为 system prompt 注入）
  └── chapters/
      └── 第01章.txt ...    # 步骤 8

提示词设计原则（适配弱 AI）：
- 纯文本，不使用 JSON / Markdown 包装
- 系统提示词 = 写作规范.txt 完整内容
- 用户提示词 = "第X章 大纲：... 前情提要：... 字数要求：..."
- 上下文只用 150 字摘要，不放上一章完整正文
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt

from .ai_client import AIError, AIClient, ChatMessage, ChatRequest
from .config_manager import DEFAULT_WORDS_PER_CHAPTER as _DEFAULT_WORDS_PER_CHAPTER


# =========================================================================
# 常量与默认值
# =========================================================================

CHAPTER_STATUS_PENDING = "pending"
CHAPTER_STATUS_WRITING = "writing"
CHAPTER_STATUS_DONE = "done"

SUMMARY_MAX_CHARS = 150  # 摘要字数硬上限

NOVEL_FILE_WORLD = "世界观.txt"
NOVEL_FILE_CHARACTERS = "角色设定.txt"
NOVEL_FILE_OUTLINE = "大纲.txt"
NOVEL_FILE_NORM = "写作规范.txt"
NOVEL_DIR_CHAPTERS = "chapters"
NOVEL_FILE_META = "metadata.json"


# =========================================================================
# 数据类
# =========================================================================


@dataclass
class ChapterMeta:
    """单个章节的元数据。"""

    number: int
    title: str = ""
    status: str = CHAPTER_STATUS_PENDING
    word_count: int = 0
    target_words: int = 0  # 目标字数（0 表示沿用全局默认）
    summary: str = ""  # 150 字摘要
    self_check_score: float = -1.0  # -1 表示未自检
    self_check_note: str = ""
    updated_at: str = ""


@dataclass
class NovelMeta:
    """整个小说的元数据（与需求文档一致）。"""

    title: str = ""
    total_chapters: int = 30
    target_total_words: int = 0  # 整本目标字数（0 表示未设定；>0 时状态栏显示进度）
    completed_chapters: int = 0
    current_model: str = ""  # 空字符串表示"未设定"；从 config 读取
    base_url: str = ""
    created_at: str = ""
    updated_at: str = ""
    chapters: dict[str, ChapterMeta] = field(default_factory=dict)  # key 是 str(number)
    direction: str = ""  # 第 2 步选定的故事方向
    writing_notes: str = ""  # 第 1 步用户偏好

    def status_summary(self) -> dict[str, int]:
        """统计各状态章节数。"""
        result = {CHAPTER_STATUS_PENDING: 0, CHAPTER_STATUS_WRITING: 0, CHAPTER_STATUS_DONE: 0}
        for ch in self.chapters.values():
            if ch.status in result:
                result[ch.status] += 1
        return result

    def total_words(self) -> int:
        return sum(ch.word_count for ch in self.chapters.values())

    def progress_percent(self) -> float | None:
        """返回 0-100 的进度百分比；未设定目标时返回 None。"""
        if self.target_total_words <= 0:
            return None
        return min(100.0, self.total_words() / self.target_total_words * 100.0)


# =========================================================================
# 项目路径解析
# =========================================================================


def project_path(root: Path, name: str) -> Path:
    """项目根目录路径。"""
    return root / "novels" / name


def project_files(root: Path, name: str) -> dict[str, Path]:
    """项目所有标准文件路径。"""
    p = project_path(root, name)
    return {
        "meta": p / NOVEL_FILE_META,
        "world": p / NOVEL_FILE_WORLD,
        "characters": p / NOVEL_FILE_CHARACTERS,
        "outline": p / NOVEL_FILE_OUTLINE,
        "norm": p / NOVEL_FILE_NORM,
        "chapters_dir": p / NOVEL_DIR_CHAPTERS,
    }


def chapter_filename(number: int) -> str:
    """章节文件名（两位补零）。"""
    return f"第{number:02d}章.txt"


# =========================================================================
# Markdown 清洗（适配弱 AI 返回的多余格式）
# =========================================================================


def clean_markdown(text: str) -> str:
    """把 AI 返回的可能含 Markdown 标记的文本清洗为纯文本。

    策略：用 markdown-it-py 解析 AST，再用 render 走 plain 模式。
    我们自己走 AST，按需提取纯文本——更可控。
    """
    if not text:
        return ""
    md = MarkdownIt()
    tokens = md.parse(text)
    out: list[str] = []
    for tok in tokens:
        ttype = tok.type
        if ttype.endswith("_open") or ttype.endswith("_close"):
            # 跳过结构性 token 的开闭标签（heading/strong/em 等）
            if ttype in ("heading_open", "paragraph_open", "paragraph_close",
                         "bullet_list_open", "bullet_list_close",
                         "ordered_list_open", "ordered_list_close",
                         "list_item_open", "list_item_close",
                         "blockquote_open", "blockquote_close",
                         "code_block_open", "code_block_close",
                         "fence_open", "fence_close"):
                if ttype == "paragraph_open":
                    out.append("\n")
                elif ttype == "heading_open":
                    out.append("\n")
                continue
            if ttype in ("strong_open", "strong_close", "em_open", "em_close"):
                continue  # 去掉粗体斜体的标记
            if ttype in ("s_open", "s_close", "link_open", "link_close"):
                continue
        elif ttype == "inline":
            # inline token 的 children 才是真正文本
            for child in tok.children or []:
                if child.type == "text":
                    out.append(child.content)
                elif child.type == "softbreak":
                    out.append("\n")
                elif child.type == "hardbreak":
                    out.append("\n")
                elif child.type == "code_inline":
                    out.append(child.content)
                # 跳过 em/strong/link_open 等
        elif ttype == "fence" or ttype == "code_block":
            # 代码块：保留内容
            out.append(tok.content)
        elif ttype == "html_block" or ttype == "html_inline":
            # 跳过 HTML 标签
            continue

    cleaned = "".join(out)
    # 进一步清理：合并多余空行
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = cleaned.strip()
    return _apply_paragraph_indent(cleaned)


def _apply_paragraph_indent(text: str) -> str:
    """每段开头空两格（两个全角空格）。智能识别结构化内容（序号、列表、分隔符），
    不对其强制缩进。

    规则：
      - 空行分隔段落
      - 每段首行加 "　　"（全角空格 ×2）
      - 以数字+标点开头（如 "1."，"2)"，"一、"）的行不缩进
      - 以破折号/星号/方括号开头的不缩进
    """
    if not text:
        return text
    # 按空行切段
    blocks = re.split(r"\n\s*\n", text)
    indented: list[str] = []
    for block in blocks:
        if not block.strip():
            indented.append(block)
            continue
        # 判断该块是否应缩进
        lines = block.split("\n")
        first = lines[0].lstrip()
        # 不做缩进的行特征
        no_indent = (
            re.match(r"^[\d一二三四五六七八九十]+[\.\)、\s]", first)  # 序号
            or re.match(r"^[#\-－—\*※◆◇●→▶✓✗\[\(（]", first)        # 符号/分隔符
            or first.startswith("---")                              # 分隔线
            or first.startswith("===")                              # 分隔标记
        )
        if no_indent:
            indented.append(block)
        else:
            # 段内每行都缩进
            sub_lines = []
            for sl in lines:
                if sl.strip():
                    sub_lines.append("　　" + sl.strip())
                else:
                    sub_lines.append(sl)
            indented.append("\n".join(sub_lines))
    return "\n\n".join(indented)


def count_chinese_words(text: str) -> int:
    """粗略字数统计（中英文都算字符，剔除空白）。"""
    return len(re.sub(r"\s+", "", text))


# =========================================================================
# 项目元数据 IO
# =========================================================================


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_meta(project_dir: Path) -> NovelMeta:
    """加载 metadata.json；不存在则返回默认值。"""
    meta_file = project_dir / NOVEL_FILE_META
    if not meta_file.exists():
        return NovelMeta()
    try:
        raw = json.loads(meta_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return NovelMeta()

    chapters_raw = raw.pop("chapters", {}) or {}
    chapters: dict[str, ChapterMeta] = {}
    for k, v in chapters_raw.items():
        try:
            chapters[k] = ChapterMeta(**v)
        except TypeError:
            continue
    try:
        meta = NovelMeta(**raw)
        meta.chapters = chapters
        return meta
    except TypeError:
        return NovelMeta()


def save_meta(project_dir: Path, meta: NovelMeta) -> None:
    """保存 metadata.json。"""
    meta.updated_at = _now_iso()
    data = asdict(meta)
    meta_file = project_dir / NOVEL_FILE_META
    meta_file.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def init_project(root: Path, name: str, total_chapters: int = 30,
                 target_total_words: int = 0) -> NovelMeta:
    """初始化新项目（创建目录、写空 metadata.json）。

    参数:
        root: 项目根目录
        name: 项目名（将作为子目录名）
        total_chapters: 章节总数
        target_total_words: 整本目标字数（0 表示不设定；>0 时同时计算每章目标字数）
    """
    files = project_files(root, name)
    files["chapters_dir"].mkdir(parents=True, exist_ok=True)
    meta = NovelMeta(
        title=name,
        total_chapters=total_chapters,
        target_total_words=target_total_words,
        completed_chapters=0,
        created_at=_now_iso(),
        updated_at=_now_iso(),
        chapters={
            str(i): ChapterMeta(
                number=i,
                title=f"第{i}章",
                status=CHAPTER_STATUS_PENDING,
                target_words=0,  # 由用户 /setup 设定，不自动推算
            )
            for i in range(1, total_chapters + 1)
        },
    )
    save_meta(project_dir := project_path(root, name), meta)
    return meta


def read_text(project_dir: Path, key: str) -> str:
    """读取项目内的文本文件；不存在返回空串。"""
    files = project_files(project_dir.parent.parent, project_dir.name)
    # 上面用了一个小技巧：根据 project_dir 反推 root 和 name
    # project_dir = root/novels/<name>
    name = project_dir.name
    root = project_dir.parent.parent
    files = project_files(root, name)
    p = files.get(key)
    if not p or not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


def write_text(project_dir: Path, key: str, content: str) -> None:
    """写入项目内的文本文件。"""
    name = project_dir.name
    root = project_dir.parent.parent
    files = project_files(root, name)
    p = files[key]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def save_chapter(project_dir: Path, number: int, content: str) -> Path:
    """保存章节正文。"""
    files = project_files(project_dir.parent.parent, project_dir.name)
    files["chapters_dir"].mkdir(parents=True, exist_ok=True)
    p = files["chapters_dir"] / chapter_filename(number)
    p.write_text(content, encoding="utf-8")
    return p


def load_chapter(project_dir: Path, number: int) -> str:
    """加载章节正文。"""
    files = project_files(project_dir.parent.parent, project_dir.name)
    p = files["chapters_dir"] / chapter_filename(number)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


# =========================================================================
# 大纲解析
# =========================================================================


_OUTLINE_PATTERNS = [
    # "第1章 标题" / "第一章 标题" / "Chapter 1: 标题"
    # 注意：标题与核心事件用第一个全角/半角冒号分隔
    re.compile(
        r"^\s*(?:第\s*(\d+)\s*章|Chapter\s+(\d+))[:：]?\s*(.+)$",
        re.IGNORECASE,
    ),
]


def parse_outline(outline_text: str) -> list[dict[str, Any]]:
    """从大纲纯文本中解析出章节列表。

    返回: [{"number": 1, "title": "...", "core_event": "..."}, ...]
    解析失败时返回空列表。
    """
    chapters: list[dict[str, Any]] = []
    lines = outline_text.splitlines()
    current: dict[str, Any] | None = None
    for line in lines:
        m = None
        for pat in _OUTLINE_PATTERNS:
            m = pat.match(line)
            if m:
                break
        if m:
            if current:
                chapters.append(current)
            num_str = m.group(1) or m.group(2)
            try:
                num = int(num_str)
            except (ValueError, TypeError):
                continue
            tail = m.group(3).strip()
            # 按第一个全角/半角冒号分隔标题与核心事件
            for sep in ("：", ":"):
                if sep in tail:
                    title, _, event = tail.partition(sep)
                    break
            else:
                title, event = tail, ""
            current = {"number": num, "title": title.strip(), "core_event": event.strip()}
        elif current and line.strip():
            current["core_event"] += line.strip() + " "
    if current:
        chapters.append(current)
    for ch in chapters:
        ch["core_event"] = ch["core_event"].strip()
    return chapters


# =========================================================================
# 核心引擎：10 步流程
# =========================================================================


def _extract_answer(text: str) -> str:
    """从 AI 输出中提取最终的答案部分。

    content 已合并 reasoning，所以直接按空行分段，取最后一个 "像答案" 的块。
    """
    if not text:
        return text
    import re as _re
    # 按空行分块，取最后有意义的块（不是纯思考）
    blocks = _re.split(r"\n\s*\n", text)
    for block in reversed(blocks):
        block = block.strip()
        if not block:
            continue
        # 跳过明显是思考的块（英文占比高、有编号列表、有关键词）
        lines = len(block.split("\n"))
        chinese = len(_re.findall(r"[一-鿿]", block))
        english = len(_re.findall(r"[a-zA-Z]", block))
        # 思考块特征：英文为主，或有很多编号行
        if english > chinese * 2 or (lines > 3 and chinese < 10):
            continue
        if block.startswith(("Here's", "Let me", "I need", "Now,", "Then", "Next")):
            continue
        return block
    return text.strip()


class NovelEngine:
    """小说生成引擎。

    所有 AI 调用通过构造时传入的 AIClient 实例发起。
    业务方（UI）通过调用对应 step 方法推进流程。
    """

    def __init__(
        self,
        ai: AIClient,
        project_dir: Path,
        model: str | None = None,
        temperature: float = 0.8,
    ) -> None:
        self.ai = ai
        self.project_dir = project_dir
        self.meta = load_meta(project_dir)
        # 模型优先级：参数 > meta 中已有 > 空（调用方应设置）
        self.model = model or self.meta.current_model or ""
        self.temperature = temperature

    # ----- 工具方法 -----

    def reload_meta(self) -> None:
        self.meta = load_meta(self.project_dir)

    def _save_meta(self) -> None:
        save_meta(self.project_dir, self.meta)

    async def _chat(self, user: str, *, stream: bool = True,
                    max_tokens: int = 8192) -> str:
        """单次对话。最多重试 2 次。"""
        for attempt in range(2):
            mt = max_tokens * (attempt + 1)
            req = ChatRequest(
                messages=[ChatMessage("user", user)],
                model=self.model,
                temperature=0.0,  # temperature=0 减少 reasoning 模型的无谓思考
                max_tokens=mt,
                stream=stream,
            )
            chunks: list[str] = []
            async for piece in self.ai.stream_with_fallback(req):
                chunks.append(piece)
            raw = "".join(chunks)
            result = _extract_answer(raw).strip()
            if result:
                return result
        return ""

    # ----- 步骤 1：收集用户偏好 -----

    def collect_preferences(self, notes: str) -> None:
        """记录用户偏好（题材/文风/字数等），保存到 metadata。"""
        self.meta.writing_notes = notes
        self._save_meta()

    # ----- 步骤 2：生成故事方向 -----

    async def generate_directions(self, count: int = 3) -> list[str]:
        """逐个调用 AI 生成故事方向。每个重试 3 次。"""
        items: list[str] = []
        for i in range(count):
            ctx = ""
            if items:
                ctx = "已有方向（请输出不同的）：\n" + "\n".join(
                    f"  {j}. {d[:60]}" for j, d in enumerate(items, 1)
                ) + "\n"
            user = (
                f"{ctx}基于以下偏好直接写一个故事方向（200字左右），不要解释：\n"
                f"{self.meta.writing_notes or '无'}"
            )
            result = ""
            for _ in range(3):
                result = await self._chat(user=user, max_tokens=8192)
                result = re.sub(r"^\s*方向[一二三四五六七八九十\d]+[:：\s]*", "", result)
                if result and len(result) >= 20:
                    items.append(result)
                    break
        if not items:
            raise AIError("所有方向生成均失败，请检查模型")
        return items

    def select_direction(self, direction: str) -> None:
        self.meta.direction = direction
        self._save_meta()

    # ----- 步骤 3：生成书名 -----

    async def generate_titles(self, count: int = 5) -> list[str]:
        """让 AI 续写编号列表。"""
        prompt = f"{self.meta.direction[:200]}\n\n给这本书起 {count} 个书名：\n"
        for i in range(count):
            prompt += f"{i+1}. "
        result = await self._chat(user=prompt, max_tokens=1024)
        items: list[str] = []
        for line in result.split("\n"):
            line = clean_markdown(line).strip()
            line = re.sub(r"^\d+[\.\)、\s]+", "", line)
            line = line.replace("**", "")
            if 2 <= len(line) <= 50:
                items.append(line)
                if len(items) >= count:
                    break
        return items[:count]

    def select_title(self, title: str) -> None:
        """选定书名。"""
        self.meta.title = title
        self._save_meta()

    # ----- 步骤 4：生成角色设定 -----

    async def generate_characters(self) -> str:
        system = "写角色设定：主角、配角、反派。姓名、年龄、性格、动机、背景。"
        user = f"书名：{self.meta.title}\n方向：{self.meta.direction}"
        result = await self._chat(user, max_tokens=1024)
        cleaned = clean_markdown(result)
        write_text(self.project_dir, "characters", cleaned)
        return cleaned

    # ----- 步骤 5：生成世界观 -----

    async def generate_world(self) -> str:
        system = "写世界观：时代背景、地理、社会结构、力量体系、核心矛盾。"
        user = (
            f"书名：{self.meta.title}\n"
            f"方向：{self.meta.direction}\n"
            f"角色：{read_text(self.project_dir, 'characters') or '暂无'}"
        )
        result = await self._chat(user, max_tokens=1024)
        cleaned = clean_markdown(result)
        write_text(self.project_dir, "world", cleaned)
        return cleaned

    # ----- 步骤 6：生成大纲 -----

    async def generate_outline(self, chapter_count: int | None = None) -> str:
        n = chapter_count or self.meta.total_chapters
        system = f"设计 {n} 章大纲。每章一行：第X章 标题及核心事件。输出全部 {n} 章。"
        user = (
            f"书名：{self.meta.title}\n"
            f"方向：{self.meta.direction}\n"
            f"角色：{read_text(self.project_dir, 'characters') or '暂无'}\n"
            f"世界观：{read_text(self.project_dir, 'world') or '暂无'}"
        )
        result = await self._chat(user, max_tokens=4096)
        cleaned = clean_markdown(result)
        write_text(self.project_dir, "outline", cleaned)
        # 同步更新 metadata 中的章节列表
        parsed = parse_outline(cleaned)
        new_chapters: dict[str, ChapterMeta] = {}
        for ch in parsed:
            num = ch["number"]
            new_chapters[str(num)] = ChapterMeta(
                number=num,
                title=ch["title"] or f"第{num}章",
                status=CHAPTER_STATUS_PENDING,
            )
        # 保留已生成章节的状态
        for k, v in self.meta.chapters.items():
            if k in new_chapters and v.status == CHAPTER_STATUS_DONE:
                new_chapters[k] = v
        if not new_chapters:
            # 解析失败：建占位
            for i in range(1, n + 1):
                new_chapters[str(i)] = ChapterMeta(
                    number=i, title=f"第{i}章", status=CHAPTER_STATUS_PENDING
                )
        self.meta.chapters = new_chapters
        self.meta.total_chapters = len(new_chapters)
        self._save_meta()
        return cleaned

    # ----- 步骤 7：固化写作规范 -----

    async def generate_writing_norm(self) -> str:
        """综合所有信息生成写作规范。"""
        system = (
            "写一份写作规范：1.主题 2.文风 3.人设红线 4.叙事视角 5.章节节奏 6.禁忌。"
        )
        user = (
            f"书名：{self.meta.title}\n"
            f"方向：{self.meta.direction}\n"
            f"角色：{read_text(self.project_dir, 'characters') or '暂无'}\n"
            f"世界观：{read_text(self.project_dir, 'world') or '暂无'}\n"
            f"大纲：{read_text(self.project_dir, 'outline') or '暂无'}"
        )
        result = await self._chat(user, max_tokens=1024)
        cleaned = clean_markdown(result)
        write_text(self.project_dir, "norm", cleaned)
        return cleaned

    # ----- 步骤 8：生成章节正文 -----

    async def write_chapter(self, number: int, target_words: int | None = None,
                            on_token: Any = None, extra_constraints: str = "") -> str:
        """生成单章正文。

        参数:
            number: 章节号
            target_words: 目标字数
            on_token: 流式回调
            extra_constraints: 自检重生成时的额外约束（如"宗门名必须为青云宗"）
          1. 系统提示词末尾追加硬约束（让 AI 时刻看到字数要求）
          2. 用户提示词明确字数 + ±10% 容差 + 提前提醒截断
          3. 容忍 ±20% 偏差（避免硬科幻等题材因世界观铺垫自然超出）

        参数:
            number: 章节号
            target_words: 目标字数（None 时从 meta.chapters[number].target_words 取，
                          再退到 WORDS_PER_CHAPTER_DEFAULT）
            on_token: 流式回调 async def on_token(chunk: str)

        返回: 完整正文（已清洗 Markdown）。
        """
        if str(number) not in self.meta.chapters:
            self.meta.chapters[str(number)] = ChapterMeta(
                number=number, title=f"第{number}章", status=CHAPTER_STATUS_PENDING
            )
        # 确定最终目标字数：参数 > 章节设定 > 全局默认
        ch_meta = self.meta.chapters[str(number)]
        final_target = (
            target_words
            if target_words is not None
            else (ch_meta.target_words or _DEFAULT_WORDS_PER_CHAPTER)
        )
        ch_meta.target_words = final_target
        ch_meta.status = CHAPTER_STATUS_WRITING
        self._save_meta()

        # 收集前情提要（前 1-2 章摘要）
        prev_summary = self._collect_prev_summary(number)

        # 从大纲提取本章核心事件
        core_event = self._extract_core_event(number)

        base_norm = read_text(self.project_dir, "norm") or (
            "你是一位网文作家，请写好这一章。"
        )
        # 1) 系统提示词末尾追加字数硬约束
        system = (
            base_norm
            + f"\n输出约 {final_target} 字。"
        )
        user = (
            f"第{number}章 大纲：{core_event}\n"
            f"前情：{prev_summary or '无'}\n"
            + (f"修正：{extra_constraints}\n" if extra_constraints else "")
        )

        req = ChatRequest(
            messages=[
                ChatMessage("system", system),
                ChatMessage("user", user),
            ],
            model=self.model,
            temperature=self.temperature,
            max_tokens=min(int(final_target * 2.0), 4096),  # 本地模型上限
            stream=True,
        )

        chunks: list[str] = []
        try:
            async for piece in self.ai.stream_with_fallback(req):
                chunks.append(piece)
                if on_token is not None:
                    try:
                        res = on_token(piece)
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception:
                        pass  # 回调异常不中断生成
        except AIError as e:
            self.meta.chapters[str(number)].status = CHAPTER_STATUS_PENDING
            self._save_meta()
            raise

        raw = "".join(chunks)
        cleaned = clean_markdown(raw)
        # 写入文件
        save_chapter(self.project_dir, number, cleaned)

        # 更新元数据
        ch = self.meta.chapters[str(number)]
        ch.word_count = count_chinese_words(cleaned)
        ch.status = CHAPTER_STATUS_DONE
        ch.updated_at = _now_iso()
        # 重算完成数
        self.meta.completed_chapters = sum(
            1 for c in self.meta.chapters.values() if c.status == CHAPTER_STATUS_DONE
        )
        self._save_meta()
        return cleaned

    # ----- 步骤 9：提炼摘要 -----

    async def summarize_chapter(self, number: int, on_token: Any = None) -> str:
        """为第 N 章提炼 150 字摘要，存入 metadata。"""
        content = load_chapter(self.project_dir, number)
        if not content:
            return ""

        system = "提炼章节摘要（150字内），保留关键人物和核心事件。"
        user = (
            f"第{number}章：\n{content}\n\n150字摘要："
        )
        req = ChatRequest(
            messages=[ChatMessage("system", system), ChatMessage("user", user)],
            model=self.model,
            temperature=0.3,
            max_tokens=512,
            stream=True,
        )
        chunks: list[str] = []
        async for piece in self.ai.stream_with_fallback(req):
            chunks.append(piece)
            if on_token is not None:
                try:
                    res = on_token(piece)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception:
                    pass
        raw = "".join(chunks)
        summary = clean_markdown(raw).strip()
        # 强制截断到 150 字
        summary = self._truncate_summary(summary, SUMMARY_MAX_CHARS)
        if str(number) in self.meta.chapters:
            self.meta.chapters[str(number)].summary = summary
            self._save_meta()
        return summary

    # ----- 步骤 10：自检 -----

    async def self_check_chapter(self, number: int, threshold: float = 0.6) -> dict[str, Any]:
        """用 AI 评判章节是否偏离人设/规范。

        返回: {"score": 0-1, "issues": [...], "passed": bool}
        """
        content = load_chapter(self.project_dir, number)
        if not content:
            return {"score": 0.0, "issues": ["章节为空"], "passed": False}

        norm = read_text(self.project_dir, "norm")
        chars = read_text(self.project_dir, "characters")
        world = read_text(self.project_dir, "world")
        outline = read_text(self.project_dir, "outline")

        system = (
            "质检章节是否合规。检查：1.人名地名一致性 2.设定一致性 3.字数偏差。"
            "输出格式：评分：0.X 问题：xxx 或 无"
        )
        user = (
            f"规范：{norm or '无'}\n"
            f"设定：{chars or '无'}\n"
            f"世界：{world or '无'}\n"
            f"大纲：{outline or '无'}\n"
            f"第{number}章正文：\n{content}"
        )
        result = await self._chat(user, stream=False, max_tokens=256)
        score, issues = self._parse_self_check_result(result)

        passed = score >= threshold
        if str(number) in self.meta.chapters:
            self.meta.chapters[str(number)].self_check_score = score
            self.meta.chapters[str(number)].self_check_note = "；".join(issues)
            self._save_meta()
        return {"score": score, "issues": issues, "passed": passed, "raw": result}

    # ----- 辅助 -----

    def _collect_prev_summary(self, number: int) -> str:
        """收集前 1-2 章的摘要 + 自检结果，拼接为前情提要。"""
        parts: list[str] = []
        for i in range(max(1, number - 2), number):
            ch = self.meta.chapters.get(str(i))
            if not ch:
                continue
            line = f"第{i}章：{ch.summary or '（无摘要）'}"
            if ch.self_check_score >= 0 and ch.self_check_score < 0.7:
                line += (
                    f"（自检{ch.self_check_score:.2f}，问题: {ch.self_check_note}；"
                    "后续章节必须注意避免这些错误）"
                )
            parts.append(line)
        text = " ".join(parts)
        if len(text) > SUMMARY_MAX_CHARS * 3:
            text = text[-SUMMARY_MAX_CHARS * 3:]
        return text.strip()

    def _extract_core_event(self, number: int) -> str:
        """从大纲.txt 中找到第 N 章的核心事件。"""
        outline_text = read_text(self.project_dir, "outline")
        for ch in parse_outline(outline_text):
            if ch["number"] == number:
                title = ch["title"]
                event = ch["core_event"]
                return f"{title}——{event}" if event else title
        return f"第{number}章"

    @staticmethod
    def _truncate_summary(text: str, limit: int) -> str:
        """摘要截断，汉字按字符计数。"""
        text = text.strip()
        if len(text) <= limit:
            return text
        return text[: limit - 1] + "…"

    @staticmethod
    def _parse_self_check_result(text: str) -> tuple[float, list[str]]:
        """解析自检输出。AI 可能不严格按格式输出，需要容错。"""
        text = clean_markdown(text).strip()
        score = 0.5
        issues: list[str] = []

        # 从文本中提取第一个浮点数作为评分（只要它不是明显不相关的大数）
        numbers = re.findall(r"(\d+\.?\d*)", text)
        for num_str in numbers:
            try:
                v = float(num_str)
                if 0.0 <= v <= 10.0 and v != 0.0:
                    score = v if v <= 1.0 else v / 10.0
                    break
                elif v == 0.0:
                    score = 0.0
                    break
            except ValueError:
                continue

        # 提取问题
        issues_text = ""
        found_issues = False
        for line in text.splitlines():
            if re.search(r"问题[:：]", line):
                parts = re.split(r"问题[:：]", line, maxsplit=1)
                if len(parts) > 1:
                    rest = parts[1].strip()
                    if rest and rest not in ("无", "None", "none", "无。", "无；"):
                        issues_text = rest
                found_issues = True
                break
        if not found_issues:
            rest_lines = [l for l in text.splitlines() if l.strip() and not re.match(r"^\d+\.?\d*$", l.strip()) and not re.search(r"评分", l)]
            if rest_lines:
                issues_text = "；".join(l.strip() for l in rest_lines)

        if issues_text:
            issues = [x.strip() for x in re.split(r"[；;]\s*", issues_text) if x.strip()]

        return score, issues