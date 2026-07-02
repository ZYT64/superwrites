# Changelog

All notable changes to this project will be documented in this file.

## [0.2.0] - 2026-07-02

### Added
- 支持本地 Ollama / 任意 OpenAI 兼容 API
- `reasoning_effort: "none"` 自动注入，关闭 Qwen3 等推理模型的思考模式
- 全流程 9 步可视化（方向→书名→角色→世界观→大纲→规范→章节→摘要→自检）
- 章节生成后自动自检，未通过弹窗询问是否重生成
- 自检结果注入到后续章节的 prompt（避免重蹈覆辙）
- 状态栏实时字数 + 进度条
- 章节流式输出到右侧预览面板（不刷屏）
- TXT + EPUB 双格式导出
- 命令行 Tab 补全
- 多小说项目管理（Ctrl+N / Ctrl+O）
- 启动自动配置检测：环境变量 > .env > config.json > 默认值

### Optimized
- 客户端响应合并 `content + reasoning + reasoning_content` 三个字段
- 启发式 `_extract_answer` 处理本地模型混乱输出
- prompt 全部精简为单行短指令
- 逐个调用 AI + 上下文防重复（方向/书名）

### Tested
- 9/9 步端到端流程通过测试
- 支持 OpenAI / DeepSeek / Ollama qwen3 / Ollama llama3

## [0.1.0] - 2026-06-30

### Added
- 初始版本
- 基础 TUI 三栏布局
- 10 步核心流程
