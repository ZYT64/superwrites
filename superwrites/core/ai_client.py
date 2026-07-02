"""AI API 客户端：兼容 OpenAI Chat Completions 协议，支持流式输出。

设计要点：
- 用 httpx.AsyncClient，避免阻塞 Textual 事件循环
- 流式解析：手动按行解析 SSE（Server-Sent Events），避免引入额外依赖
- 自动降级：流式失败时（连接被拒、返回非 SSE 等）自动重试非流式
- 支持自定义 base_url，可适配 DeepSeek / 通义千问 / Moonshot / Ollama 等
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class ChatMessage:
    """对话消息。"""

    role: str  # system | user | assistant
    content: str


@dataclass
class ChatRequest:
    """单次对话请求。"""

    messages: list[ChatMessage]
    model: str = ""  # 由调用方填入，不硬编码
    temperature: float = 0.8
    max_tokens: int = 4096
    stream: bool = True
    disable_thinking: bool = True  # 本地推理模型默认关闭思考


@dataclass
class Usage:
    """Token 用量统计（流式结束时汇总）。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class ChatResponse:
    """非流式响应。"""

    content: str
    model: str = ""
    usage: Usage = field(default_factory=Usage)
    finish_reason: str = ""


class AIError(RuntimeError):
    """AI 调用错误（API 错误、网络错误等）。"""


class AIClient:
    """AI 客户端。

    用法:
用法:
        client = AIClient(api_key="sk-xxx", base_url="https://api.deepseek.com/v1")
        async for chunk in client.stream_chat(req):
            print(chunk, end="", flush=True)
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com/v1",
        timeout: float = 600.0,
        max_retries: int = 3,
    ) -> None:
        if not api_key:
            raise AIError("API Key 不能为空")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    async def close(self) -> None:
        """关闭底层 HTTP 连接池。"""
        await self._client.aclose()

    async def __aenter__(self) -> "AIClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    def _endpoint(self, path: str) -> str:
        return f"{self.base_url}{path}"

    @staticmethod
    def _serialize_messages(messages: list[ChatMessage]) -> list[dict[str, str]]:
        return [{"role": m.role, "content": m.content} for m in messages]

    async def chat(self, req: ChatRequest) -> ChatResponse:
        """非流式对话。

        即使外部请求 stream=True，内部出错时也会降级到这里。
        """
        payload = {
            "model": req.model,
            "messages": self._serialize_messages(req.messages),
            "temperature": req.temperature,
            "max_tokens": req.max_tokens,
            "stream": False,
        }
        if req.disable_thinking:
            payload["reasoning_effort"] = "none"
        try:
            resp = await self._client.post(self._endpoint("/chat/completions"), json=payload)
        except httpx.HTTPError as e:
            raise AIError(f"网络错误: {e}") from e

        if resp.status_code >= 400:
            raise AIError(f"API 错误 {resp.status_code}: {resp.text[:500]}")

        data = resp.json()
        try:
            choice = data["choices"][0]
            msg = choice["message"]
            # 兼容 reasoning 模型：Ollama 用 "reasoning"，OpenAI 用 "reasoning_content"
            content = (msg.get("content") or "") + (msg.get("reasoning") or "") + (msg.get("reasoning_content") or "")
        except (KeyError, IndexError, TypeError) as e:
            raise AIError(f"响应格式异常: {data!r}") from e

        usage_data = data.get("usage", {}) or {}
        usage = Usage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
        )
        return ChatResponse(
            content=content,
            model=data.get("model", req.model),
            usage=usage,
            finish_reason=choice.get("finish_reason", ""),
        )

    async def stream_chat(self, req: ChatRequest) -> AsyncIterator[str]:
        """流式对话，逐 chunk yield 文本片段。

        失败时（非 SSE / 网络错误）抛 AIError，调用方可降级到 chat()。
        """
        if not req.stream:
            resp = await self.chat(req)
            yield resp.content
            return

        payload = {
            "model": req.model,
            "messages": self._serialize_messages(req.messages),
            "temperature": req.temperature,
            "max_tokens": req.max_tokens,
            "stream": True,
        }
        if req.disable_thinking:
            payload["reasoning_effort"] = "none"

        try:
            async with self._client.stream(
                "POST", self._endpoint("/chat/completions"), json=payload
            ) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", errors="replace")
                    raise AIError(f"API 错误 {resp.status_code}: {body[:500]}")

                # 按行解析 SSE
                buffer = ""
                async for chunk in resp.aiter_text():
                    buffer += chunk
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if not line:
                            continue
                        # SSE: "data: {...json...}"
                        if not line.startswith("data:"):
                            continue
                        data_str = line[len("data:"):].strip()
                        if data_str == "[DONE]":
                            return
                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            # 部分 chunk 跨行等情况，静默忽略
                            continue
                        try:
                            choice = data["choices"][0]
                            delta = choice.get("delta", {})
                            # 兼容 Ollama "reasoning" 和 OpenAI "reasoning_content"
                            piece = (delta.get("content") or "") + (delta.get("reasoning") or "") + (delta.get("reasoning_content") or "")
                            if piece:
                                yield piece
                        except (KeyError, IndexError, TypeError):
                            # 异常 chunk 跳过
                            continue
        except httpx.HTTPError as e:
            raise AIError(f"流式连接错误: {e}") from e

    async def stream_with_fallback(self, req: ChatRequest) -> AsyncIterator[str]:
        """流式 + 自动降级到非流式。

        优先尝试流式；失败时整体回退到非流式，再 yield 完整内容。
        """
        try:
            async for piece in self.stream_chat(req):
                yield piece
        except AIError as e:
            # 流式失败 → 非流式重试一次
            err_msg = str(e)
            if req.stream:
                req_fallback = ChatRequest(
                    messages=req.messages,
                    model=req.model,
                    temperature=req.temperature,
                    max_tokens=req.max_tokens,
                    stream=False,
                )
                try:
                    resp = await self.chat(req_fallback)
                    yield resp.content
                    return
                except (AIError, RuntimeError) as e2:
                    raise AIError(
                        f"流式失败({err_msg})，非流式重试也失败: {e2}"
                    ) from e2
            raise