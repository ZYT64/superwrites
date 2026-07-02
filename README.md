# SuperWrites

> TUI 网文生成器 —— 终端里的 AI 写作伙伴

基于 [Textual](https://textual.textualize.io/) 的终端 AI 写作工具，10 步流程从故事方向到章节生成一气呵成。支持 OpenAI 兼容 API（DeepSeek、OpenAI、本地 Ollama 等）。

![demo](https://raw.githubusercontent.com/wiki/yourname/superwrites/demo.png)

## ✨ 特性

- **三栏 IDE 风格 TUI**：侧边大纲树 / 右侧预览 / 右下日志+命令栏
- **10 步完整流程**：从故事方向到章节生成，全程可视化
- **多项目管理**：可同时维护多个小说项目，Ctrl+N/O 切换
- **多 AI 服务商**：支持 DeepSeek / OpenAI / Ollama 等任何 OpenAI 兼容 API
- **流式输出**：AI 内容逐字追加到右侧预览面板，体感接近 ChatGPT
- **多格式导出**：TXT（含章节分隔符）+ EPUB（含目录）
- **自动重试**：AI 调用失败时弹窗询问，最大重试次数可配
- **自动自检**：生成章节后用 AI 评判，未通过则根据问题重生成
- **本地模型友好**：已专门优化 Ollama / 本地推理模型（`reasoning_effort: "none"` 关闭思考）
- **命令补全**：输入命令时实时显示匹配项，Tab 补全

## 📦 安装

```bash
git clone https://github.com/yourname/superwrites.git
cd superwrites

# 推荐：虚拟环境
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # macOS/Linux

pip install -e .
```

或直接安装依赖：

```bash
pip install textual rich httpx ebooklib
```

## 🚀 快速开始

```bash
python -m superwrites.tui_app
```

启动后：
1. **首次启动**会弹窗引导输入 API Key（不会写入 config.json，避免泄露）
2. **Ctrl+N** 新建小说项目（输入项目名、章节数、目标字数）
3. **Ctrl+O** 打开已有项目
4. 输入 `/setup` 按回车 → 启动规划向导
5. 左侧选中章节按回车 → 触发 AI 生成

## ⌨️ 快捷键

| 按键 | 功能 |
|------|------|
| `Tab` / `方向键` | 切换面板焦点 |
| `Enter` | 左侧选中章节时无操作（需输入命令） |
| `Ctrl+S` | 保存 metadata |
| `Ctrl+E` | 导出（弹窗选择 TXT / EPUB / 两者） |
| `Ctrl+N` | 新建项目 |
| `Ctrl+O` | 打开已有项目 |
| `Ctrl+Q` | 退出 |
| `/` | 聚焦命令输入框 |
| `Esc` | 关闭当前弹窗 |

## 📋 内置命令

| 命令 | 说明 |
|------|------|
| `/setup` | 启动规划向导（10 步流程前 7 步） |
| `/write N` | 生成第 N 章（用章节设定字数） |
| `/write N M` | 生成第 N 章，目标 M 字 |
| `/write N-M` | 批量生成第 N-M 章 |
| `/write N-M K` | 批量生成，每章 K 字 |
| `/export` | 弹窗选择导出格式和目录 |
| `/export txt` | 直接导出 TXT |
| `/export epub` | 直接导出 EPUB |
| `/model <name>` | 切换 AI 模型 |
| `/help` | 显示帮助 |

输入命令时实时显示匹配项。**Tab** 接受补全。

## 📂 目录结构

```
SuperWrites/
├── superwrites/                # 源代码
│   ├── tui_app.py             # 主程序入口
│   ├── core/
│   │   ├── ai_client.py       # OpenAI 兼容 API 客户端
│   │   ├── config_manager.py  # 全局配置管理
│   │   ├── novel_engine.py    # 10 步核心引擎
│   │   └── exporter.py        # TXT / EPUB 导出
│   └── widgets/               # 4 个 UI 组件
│       ├── sidebar.py         # 左侧大纲树
│       ├── preview_panel.py   # 右侧预览
│       ├── log_panel.py       # 右下日志
│       └── input_bar.py       # 命令输入 + Tab 补全
├── tests/                      # 端到端测试
│   └── test_final3.py         # 9 步完整流程测试
├── novels/                     # 小说项目（运行时生成，git 忽略）
├── config.example.json        # 配置示例
├── config.json                 # 用户配置（含 API Key，git 忽略）
├── pyproject.toml             # 项目元数据
├── LICENSE                     # MIT
└── README.md
```

## 🔧 配置

启动后自动创建 `config.json`，完整配置说明见 [`config.example.json`](config.example.json)。

### DeepSeek

```json
{
  "api_key": "sk-xxxxxxxx",
  "base_url": "https://api.deepseek.com/v1",
  "model": "deepseek-chat"
}
```

### OpenAI

```json
{
  "api_key": "sk-xxxxxxxx",
  "base_url": "https://api.openai.com/v1",
  "model": "gpt-4o-mini"
}
```

### 本地 Ollama（推荐）

```json
{
  "api_key": "ollama",
  "base_url": "http://localhost:11434/v1",
  "model": "qwen3"
}
```

> 启动 Ollama：`ollama serve`（默认端口 11434）。本地推理模型需传 `reasoning_effort: "none"` 关闭思考模式（本程序已自动处理）。

### 环境变量优先

- `DEEPSEEK_API_KEY` > `config.json` > 默认值
- `DEEPSEEK_BASE_URL` 同上
- `DEEPSEEK_MODEL` 同上

## 🧪 测试

```bash
python tests/test_final3.py
```

应输出 `=== 全部 9/9 步通过 ===`，覆盖方向生成、书名生成、角色、世界观、大纲、规范、章节、摘要、自检全流程。

## 🛠 故障排查

| 问题 | 解决方案 |
|------|---------|
| `NoActiveWorker` 弹窗 | 升级 Textual: `pip install -U textual` |
| 预览面板中文重叠 | Textual 已知 bug，建议升级到 ≥0.78 |
| `ollama` 模型书名为空 | 本程序已自动加 `reasoning_effort: "none"`，升级 Ollama 到 ≥0.21 |
| 章节字数超/欠 | 检查 `default_target_total_words / total_chapters` 是否合理 |
| 自检一直不通过 | 调高 `self_check_threshold`（如 0.4） |

## 🤝 贡献

欢迎 PR 和 Issue！

## 📄 许可证

MIT
