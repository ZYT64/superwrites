"""SuperWrites 端到端测试：覆盖 10 步流程。

运行方法：
    1. 启动 Ollama: ollama serve
    2. python tests/test_e2e.py
    或 配置 DeepSeek/OpenAI key 到 config.json 后运行

要求：能调通 AI 接口
"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")

from superwrites.core.ai_client import AIClient
from superwrites.core.novel_engine import NovelEngine, init_project


def load_config() -> dict:
    cfg = Path("config.json")
    if not cfg.exists():
        print("ERROR: config.json 不存在，请先运行 TUI 启动一次以生成模板")
        sys.exit(1)
    return json.loads(cfg.read_text(encoding="utf-8"))


async def main():
    cfg = load_config()
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        ai = AIClient(
            api_key=cfg["api_key"],
            base_url=cfg["base_url"],
            timeout=600,
        )
        init_project(td_path, "test", total_chapters=3)
        engine = NovelEngine(
            ai,
            td_path / "novels" / "test",
            model=cfg["model"],
            temperature=0.0,
        )
        engine.collect_preferences("都市言情")

        steps: list[str] = []

        print("1/9 方向...", flush=True)
        dirs = await engine.generate_directions(count=2)
        assert dirs, "方向空"
        steps.append("方向")
        engine.select_direction(dirs[0])

        print("2/9 书名...", flush=True)
        titles = await engine.generate_titles(count=3)
        assert titles, "书名空"
        steps.append("书名")
        engine.select_title(titles[0])

        print("3/9 角色...", flush=True)
        await engine.generate_characters()
        steps.append("角色")

        print("4/9 世界观...", flush=True)
        await engine.generate_world()
        steps.append("世界观")

        print("5/9 大纲...", flush=True)
        await engine.generate_outline()
        steps.append("大纲")

        print("6/9 规范...", flush=True)
        await engine.generate_writing_norm()
        steps.append("规范")

        print("7/9 第1章...", flush=True)
        content = await engine.write_chapter(1, target_words=200)
        assert content, "章节空"
        steps.append("章节")

        print("8/9 摘要...", flush=True)
        await engine.summarize_chapter(1)
        steps.append("摘要")

        print("9/9 自检...", flush=True)
        await engine.self_check_chapter(1, 0.6)
        steps.append("自检")

        await ai.close()
        print(f"\n=== 全部 {len(steps)}/9 步通过: {steps} ===", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
