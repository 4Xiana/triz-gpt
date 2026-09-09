# -*- coding: utf-8 -*-
"""OpenAI 兼容接口客户端（默认对接 MiniMax 开放平台）。

MiniMax 与 DeepSeek、智谱、本地 vLLM 等均提供 OpenAI 兼容接口，
通过环境变量配置即可切换：
- LLM_BASE_URL：默认 https://api.minimaxi.com/v1
- LLM_API_KEY / MINIMAX_API_KEY：API 密钥
- LLM_MODEL：对话模型名（默认 MiniMax-M3）
- LLM_EMBEDDING_MODEL：嵌入模型名（不配置则使用本地向量兜底；
  MiniMax 平台填 embo-01，其 /embeddings 为原生格式，本类已自动适配）
- LLM_DISABLE_THINKING：默认 "0"（保留思考模式，思考过程以流式事件实时
  推送给前端折叠展示）；设为 "1" 可关闭思考以换取更快响应
"""

import hashlib
import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

import numpy as np
import requests

from ..triz.vectors import local_embed
from .base import BaseLLM

_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data"))


class OpenAICompatLLM(BaseLLM):
    def __init__(self, api_key: str, base_url: str = "https://api.minimaxi.com/v1",
                 model: str = "MiniMax-M3", embedding_model: Optional[str] = None,
                 timeout: int = 300, disable_thinking: Optional[bool] = None):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.embedding_model = embedding_model
        self.timeout = timeout
        if disable_thinking is None:
            # 默认保留思考模式（思考流实时推送给前端展示）；设 LLM_DISABLE_THINKING=1 可关闭
            disable_thinking = os.environ.get("LLM_DISABLE_THINKING", "0").strip() == "1"
        self.disable_thinking = disable_thinking
        self.name = f"openai-compat:{model}"
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        # 嵌入向量持久化缓存：静态知识库只需在线向量化一次，词袋关键词跨轮次去重
        self._embed_lock = threading.Lock()
        self._embed_cache: Dict[str, np.ndarray] = {}
        self._cache_loaded = False
        safe_model = "".join(c if c.isalnum() or c in "-_" else "_"
                             for c in (embedding_model or "none"))
        self._cache_path = os.path.join(_DATA_DIR, f"embed_cache_{safe_model}.npz")

    # ------------------------------------------------------------------
    def heartbeat(self) -> None:
        """启动时轻量探活，失败则由工厂方法回退演示模型。"""
        resp = requests.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers,
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": "ping，请回复 pong"}],
                "max_tokens": 16,
                "temperature": 0.1,
            },
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json()
        if "choices" not in body:
            raise RuntimeError(f"异常响应：{str(body)[:200]}")

    # ------------------------------------------------------------------
    def _build_payload(self, messages: List[Dict[str, str]], temperature: float,
                       json_format: bool, stream: bool) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if stream:
            payload["stream"] = True
        if json_format:
            payload["response_format"] = {"type": "json_object"}
        if self.disable_thinking:
            payload["thinking"] = {"type": "disabled"}
        return payload

    def _post_chat(self, payload: Dict[str, Any], stream: bool = False) -> requests.Response:
        """发起对话请求；若供应商不识别 thinking/response_format（400），逐步降级重试。"""
        resp = requests.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers,
            json=payload,
            timeout=self.timeout,
            stream=stream,
        )
        if resp.status_code == 400:
            degraded = dict(payload)
            if "thinking" in degraded:
                degraded.pop("thinking")
                resp = requests.post(
                    f"{self.base_url}/chat/completions", headers=self._headers,
                    json=degraded, timeout=self.timeout, stream=stream)
            if resp.status_code == 400 and "response_format" in degraded:
                degraded.pop("response_format")
                resp = requests.post(
                    f"{self.base_url}/chat/completions", headers=self._headers,
                    json=degraded, timeout=self.timeout, stream=stream)
        resp.raise_for_status()
        return resp

    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.7,
             meta: Optional[Dict[str, Any]] = None, json_format: bool = False) -> str:
        payload = self._build_payload(messages, temperature, json_format, stream=False)
        last_exc = None
        for attempt in range(2):  # 网络抖动/超时自动重试一次
            try:
                resp = self._post_chat(payload)
                body = resp.json()
                return body["choices"][0]["message"]["content"]
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_exc = exc
                continue
        raise last_exc

    def chat_stream(self, messages: List[Dict[str, str]], temperature: float = 0.7,
                    meta: Optional[Dict[str, Any]] = None,
                    json_format: bool = False):
        payload = self._build_payload(messages, temperature, json_format, stream=True)
        try:
            resp = self._post_chat(payload, stream=True)
            for raw_line in resp.iter_lines():
                if not raw_line:
                    continue
                line = raw_line.decode("utf-8", errors="ignore").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    delta = chunk["choices"][0].get("delta", {})
                    piece = delta.get("content")
                    if piece:
                        yield piece
                except (ValueError, KeyError, IndexError):
                    continue
        except (requests.Timeout, requests.ConnectionError) as exc:
            # 流式通道异常时退化为一次性请求，保证流程不中断
            print(f"[LLM] 流式请求失败，回退普通请求：{exc}")
            yield self.chat(messages, temperature=temperature, meta=meta,
                            json_format=json_format)

    # ------------------------------------------------------------------
    # 嵌入：MiniMax embo-01 使用原生 /embeddings 协议（texts/type 入参，vectors 出参），
    # 与 OpenAI 协议（input/data）不同；失败时静默兜底本地向量。
    # ------------------------------------------------------------------
    def _load_cache(self) -> None:
        if self._cache_loaded:
            return
        self._cache_loaded = True
        try:
            if os.path.exists(self._cache_path):
                data = np.load(self._cache_path, allow_pickle=False)
                for key, vec in zip(data["keys"], data["vecs"]):
                    self._embed_cache[str(key)] = np.asarray(vec, dtype=np.float64)
                print(f"[LLM] 嵌入缓存已加载：{len(self._embed_cache)} 条")
        except Exception as exc:  # noqa: BLE001
            print(f"[LLM] 嵌入缓存加载失败：{exc}")

    def _save_cache(self) -> None:
        try:
            os.makedirs(_DATA_DIR, exist_ok=True)
            keys = list(self._embed_cache.keys())
            vecs = np.asarray([self._embed_cache[k] for k in keys], dtype=np.float64)
            tmp_path = self._cache_path + ".tmp.npz"
            np.savez(tmp_path, keys=np.asarray(keys), vecs=vecs)
            os.replace(tmp_path, self._cache_path)
        except Exception as exc:  # noqa: BLE001
            print(f"[LLM] 嵌入缓存保存失败：{exc}")

    def embed(self, texts: List[str]) -> np.ndarray:
        if not self.embedding_model:
            return local_embed(texts)
        self._load_cache()
        results: List[Optional[np.ndarray]] = [None] * len(texts)
        missing = []  # (序号, 缓存键, 原文)
        for i, text in enumerate(texts):
            key = hashlib.sha1(
                f"{self.embedding_model}\x00{text}".encode("utf-8")).hexdigest()
            cached = self._embed_cache.get(key)
            if cached is not None:
                results[i] = cached
            else:
                missing.append((i, key, text))

        if missing:
            t0 = time.perf_counter()
            try:
                # MiniMax 约定：db=文档/知识块，query=检索语句
                embed_type = "query" if len(texts) == 1 else "db"
                resp = requests.post(
                    f"{self.base_url}/embeddings",
                    headers=self._headers,
                    json={"model": self.embedding_model, "type": embed_type,
                          "texts": [m[2] for m in missing]},
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                body = resp.json()
                vecs = body.get("vectors")
                status = (body.get("base_resp") or {}).get("status_code", 0)
                if not vecs or status != 0:
                    raise RuntimeError(f"embedding 异常响应：{str(body)[:200]}")
                if len(vecs) != len(missing):
                    raise RuntimeError(
                        f"embedding 返回条数不符：期望 {len(missing)}，实际 {len(vecs)}")
                with self._embed_lock:
                    for (idx, key, _), vec in zip(missing, vecs):
                        arr = np.asarray(vec, dtype=np.float64)
                        self._embed_cache[key] = arr
                        results[idx] = arr
                    self._save_cache()
                print(f"[LLM] embed 在线请求 {len(missing)} 条（{embed_type}），"
                      f"耗时 {time.perf_counter() - t0:.2f}s")
            except Exception as exc:  # noqa: BLE001
                # 嵌入接口不可用时静默兜底本地向量，保证主流程不中断
                print(f"[LLM] 在线嵌入不可用，使用本地向量：{exc}")
                return local_embed(texts)
        return np.asarray(results, dtype=np.float64)
