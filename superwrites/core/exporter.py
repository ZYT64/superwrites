"""导出器：把项目目录导出为 TXT / EPUB。"""

from __future__ import annotations

from pathlib import Path

from ebooklib import epub

from .novel_engine import (
    NOVEL_FILE_META,
    NovelMeta,
    chapter_filename,
    load_chapter,
    project_files,
)


# =========================================================================
# TXT 导出
# =========================================================================

CHAPTER_SEPARATOR = "\n\n========== 第 {n} 章 {title} ==========\n\n"


def export_txt(project_dir: Path, meta: NovelMeta, out_dir: Path | None = None) -> Path:
    """导出整本小说为单个 TXT 文件。

    参数:
        out_dir: 输出目录；None 则存到项目根目录。
    """
    files = project_files(project_dir.parent.parent, project_dir.name)
    target_dir = out_dir or files["chapters_dir"].parent
    out_path = target_dir / f"{meta.title or project_dir.name}.txt"

    lines: list[str] = []
    # 元数据头
    lines.append(f"书名：{meta.title}")
    lines.append(f"总章节：{meta.total_chapters}")
    lines.append(f"已完成：{meta.completed_chapters}")
    lines.append(f"创建时间：{meta.created_at}")
    lines.append(f"导出时间：{meta.updated_at}")
    lines.append("")
    lines.append("=" * 60)
    lines.append("")

    # 按章节号排序输出
    sorted_nums = sorted(int(k) for k in meta.chapters.keys())
    for n in sorted_nums:
        ch = meta.chapters[str(n)]
        sep = CHAPTER_SEPARATOR.format(n=n, title=ch.title or f"第{n}章")
        lines.append(sep)
        body = load_chapter(project_dir, n)
        if body:
            lines.append(body)
        else:
            lines.append("（本章尚未生成）")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


# =========================================================================
# EPUB 导出
# =========================================================================


def _chapter_to_html(title: str, content: str) -> str:
    """把章节内容包成 HTML。

    注意：不包含 <?xml ?> 声明（lxml 不支持 unicode 字符串带 encoding 声明）。
    """
    # 把段落按双换行分段
    paragraphs = content.split("\n\n") if content else ["（本章尚未生成）"]
    body = "\n".join(f"<p>{_escape(p.strip())}</p>" for p in paragraphs if p.strip())
    if not body:
        body = "<p>（本章尚未生成）</p>"
    return f"""<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" lang="zh-CN">
<head>
  <title>{_escape(title)}</title>
  <meta charset="utf-8" />
</head>
<body>
  <h1>{_escape(title)}</h1>
  {body}
</body>
</html>
"""


def _escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def export_epub(project_dir: Path, meta: NovelMeta, out_dir: Path | None = None) -> Path:
    """导出整本小说为 EPUB 文件。"""
    files = project_files(project_dir.parent.parent, project_dir.name)
    target_dir = out_dir or files["chapters_dir"].parent
    out_path = target_dir / f"{meta.title or project_dir.name}.epub"

    book = epub.EpubBook()
    book.set_identifier(f"superwrites-{project_dir.name}")
    book.set_title(meta.title or project_dir.name)
    book.set_language("zh")
    book.add_author("SuperWrites 用户")

    # 添加章节
    chapters: list[epub.EpubHtml] = []
    sorted_nums = sorted(int(k) for k in meta.chapters.keys())
    for n in sorted_nums:
        ch = meta.chapters[str(n)]
        title = ch.title or f"第{n}章"
        body = load_chapter(project_dir, n)
        c = epub.EpubHtml(
            title=title,
            file_name=f"chap_{n:03d}.xhtml",
            lang="zh",
        )
        # 即使空章节也要有最小占位 HTML（否则 lxml 解析会失败）
        c.content = _chapter_to_html(title, body or "（本章尚未生成）")
        book.add_item(c)
        chapters.append(c)

    # 目录
    book.toc = tuple(chapters)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    # 默认 spine
    book.spine = ["nav", *chapters]

    # 写入文件
    epub.write_epub(str(out_path), book)
    return out_path