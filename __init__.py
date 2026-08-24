"""Manual inbound buffering for Hermes gateway.

Commands:
  /begin   Start buffering normal inbound messages for the current session.
  /over    Flush the next buffered batch as one prompt and let Hermes process it.
  /cancel  Discard the current buffer.
  /preview Show a short preview of the current buffer.

This plugin uses Hermes' pre_gateway_dispatch hook so ordinary buffered
messages never reach the agent until /over rewrites the inbound event.
"""

from __future__ import annotations

import logging
import threading
import time
import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from hermes_cli.plugins import get_plugin_manager

logger = logging.getLogger(__name__)


@dataclass
class BufferedMessage:
    text: str = ""
    media_urls: List[str] = field(default_factory=list)
    media_types: List[str] = field(default_factory=list)


@dataclass
class BufferState:
    started_at: float = field(default_factory=time.time)
    messages: List[BufferedMessage] = field(default_factory=list)


_lock = threading.Lock()
_buffers: Dict[str, BufferState] = {}
_ctx = None


def _setting(name: str, default: Any) -> Any:
    if _ctx is None:
        return default
    try:
        return _ctx.get_config(name, default)
    except Exception:
        return default


def _command_name(name: str, default: str) -> str:
    value = str(_setting(name, default) or default).strip().lower()
    return value.lstrip("/") or default


def _commands() -> Tuple[str, str, str, str]:
    return (
        _command_name("begin_command", "begin"),
        _command_name("over_command", "over"),
        _command_name("cancel_command", "cancel"),
        _command_name("preview_command", "preview"),
    )


def _truthy_setting(name: str, default: bool) -> bool:
    value = _setting(name, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _int_setting(name: str, default: int) -> int:
    try:
        return max(1, int(_setting(name, default)))
    except Exception:
        return default


def _session_key(event: Any) -> str:
    src = getattr(event, "source", None)
    if src is None:
        return "unknown"
    profile = getattr(src, "profile", None) or "default"
    platform = getattr(getattr(src, "platform", None), "value", None) or str(getattr(src, "platform", "unknown"))
    chat_id = getattr(src, "chat_id", None) or ""
    thread_id = getattr(src, "thread_id", None) or ""
    user_id = getattr(src, "user_id", None) or ""
    chat_type = getattr(src, "chat_type", None) or ""
    if chat_type == "dm":
        return f"{profile}:{platform}:dm:{chat_id}:{thread_id}:{user_id}"
    return f"{profile}:{platform}:{chat_id}:{thread_id}"


def _event_text(event: Any) -> str:
    return str(getattr(event, "text", "") or "")


def _event_media(event: Any) -> Tuple[List[str], List[str]]:
    urls = list(getattr(event, "media_urls", None) or [])
    types = list(getattr(event, "media_types", None) or [])
    return urls, types


def _parse_command(text: str) -> Tuple[Optional[str], str]:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None, ""
    head, _, rest = stripped[1:].partition(" ")
    command = head.split("@", 1)[0].strip().lower()
    return command or None, rest.strip()


def _format_message(index: int, message: BufferedMessage) -> str:
    parts = [f"【消息 {index}】"]
    text = message.text.strip()
    if text:
        parts.append(text)
    if message.media_urls:
        media_lines = []
        for i, path in enumerate(message.media_urls):
            mtype = message.media_types[i] if i < len(message.media_types) else ""
            label = mtype or "附件"
            media_lines.append(f"- {label}: {path}")
        parts.append("附件：\n" + "\n".join(media_lines))
    return "\n".join(parts)


def _buffer_stats(state: BufferState) -> Tuple[int, int]:
    return len(state.messages), sum(len(m.text) for m in state.messages)


def _buffer_media(state: BufferState) -> Tuple[List[str], List[str]]:
    urls: List[str] = []
    types: List[str] = []
    for message in state.messages:
        urls.extend(message.media_urls)
        types.extend(message.media_types)
    return urls, types


def _messages_media(messages: List[BufferedMessage]) -> Tuple[List[str], List[str]]:
    urls: List[str] = []
    types: List[str] = []
    for message in messages:
        urls.extend(message.media_urls)
        types.extend(message.media_types)
    return urls, types


def _reply(event: Any, gateway: Any, content: str) -> None:
    if not _truthy_setting("ack_messages", True):
        return
    try:
        src = getattr(event, "source", None)
        adapter = None
        if gateway is not None and src is not None:
            adapters = getattr(gateway, "adapters", {}) or {}
            adapter = adapters.get(getattr(src, "platform", None))
        if adapter is None or src is None:
            return
        task = adapter.send(
            chat_id=getattr(src, "chat_id", ""),
            content=content,
            reply_to=getattr(event, "message_id", None),
        )
        asyncio.get_running_loop().create_task(task)
    except Exception:
        return


def _preview_text(state: BufferState, limit: int = 1200) -> str:
    count, chars = _buffer_stats(state)
    body = "\n\n".join(_format_message(i + 1, msg) for i, msg in enumerate(state.messages))
    if len(body) > limit:
        body = body[:limit].rstrip() + "\n..."
    return f"手动消息缓冲区：共 {count} 条，{chars} 个字符。\n\n{body or '（空）'}"


def _control_response(event: Any, gateway: Any, text: str) -> Dict[str, str]:
    _reply(event, gateway, text)
    return {"action": "skip", "reason": "manual-buffer-control", "text": text}


def _on_pre_gateway_dispatch(event: Any, gateway: Any = None, **_: Any) -> Optional[Dict[str, str]]:
    text = _event_text(event)
    command, _args = _parse_command(text)
    begin_cmd, over_cmd, cancel_cmd, preview_cmd = _commands()
    key = _session_key(event)

    if command == begin_cmd:
        with _lock:
            _buffers[key] = BufferState()
        return _control_response(
            event,
            gateway,
            f"已开始手动收集消息。请继续发送内容，发送完用 /{over_cmd} 一起处理。"
        )

    if command == cancel_cmd:
        with _lock:
            state = _buffers.pop(key, None)
        if state is None:
            return _control_response(event, gateway, "当前没有待取消的手动消息缓冲区。")
        count, chars = _buffer_stats(state)
        return _control_response(event, gateway, f"已取消手动收集，丢弃 {count} 条消息、{chars} 个字符。")

    if command == preview_cmd:
        with _lock:
            state = _buffers.get(key)
            if state is None:
                return _control_response(event, gateway, "当前没有手动消息缓冲区。")
            preview = _preview_text(state)
        return _control_response(event, gateway, preview)

    if command == over_cmd:
        with _lock:
            state = _buffers.get(key)
            if state is not None:
                batch_size = _int_setting("flush_batch_size", 10)
                batch = state.messages[:batch_size]
                del state.messages[:batch_size]
                if not state.messages:
                    _buffers.pop(key, None)
        if state is None:
            return _control_response(event, gateway, f"当前没有手动消息缓冲区，请先用 /{begin_cmd} 开始。")
        if not batch:
            return _control_response(event, gateway, "手动缓冲区为空，没有可处理的消息。")
        media_urls, media_types = _messages_media(batch)
        try:
            event.media_urls = media_urls
            event.media_types = media_types
        except Exception:
            pass
        combined = "\n\n".join(_format_message(i + 1, msg) for i, msg in enumerate(batch))
        prompt = (
            "用户以一批手动收集的形式发送了以下消息，请把它们当作一个整体请求来处理。"
            "本条消息的附件已随本轮一并提交。\n\n"
            f"{combined}"
        )

        # If more batches remain, keep them buffered and auto-inject the next batch
        # after a short delay without requiring the user to type /over again.
        with _lock:
            leftover = _buffers.get(key)
            if leftover is not None and leftover.messages:
                chunks = []
                while leftover.messages:
                    chunks.append(leftover.messages[:batch_size])
                    del leftover.messages[:batch_size]
                if not leftover.messages:
                    _buffers.pop(key, None)
            else:
                chunks = []

        if chunks:
            manager = get_plugin_manager()
            plugin_id = _ctx.plugin_id

            async def _drain_remaining():
                for i, chunk in enumerate(chunks):
                    await asyncio.sleep(0.15 * (i + 1))
                    c_text = "\n\n".join(
                        _format_message(j + 1, m) for j, m in enumerate(chunk)
                    )
                    try:
                        manager.inject_gateway_message(
                            session_key=key,
                            content=c_text,
                            plugin_id=plugin_id,
                            media_urls=_messages_media(chunk)[0],
                            media_types=_messages_media(chunk)[1],
                        )
                    except Exception:
                        logger.warning(
                            "manual-buffer auto-drain failed for session %s", key,
                            exc_info=True,
                        )

            try:
                asyncio.get_running_loop().create_task(_drain_remaining())
            except RuntimeError:
                pass

        return {"action": "rewrite", "text": prompt}

    with _lock:
        state = _buffers.get(key)
        if state is None:
            return None
        if command is not None:
            # 缓冲区处于收集中时，其他命令照常放行。
            return None
        max_messages = _int_setting("max_messages", 100)
        max_chars = _int_setting("max_chars", 50000)
        batch_size = _int_setting("flush_batch_size", 10)
        current_count, current_chars = _buffer_stats(state)
        incoming = text.strip()
        media_urls, media_types = _event_media(event)
        if not incoming and not media_urls:
            return {"action": "skip", "reason": "manual-buffer-empty-message"}
        if current_count >= max_messages or current_chars + len(incoming) > max_chars:
            return _control_response(
                event,
                gateway,
                f"手动缓冲区已达上限（{current_count} 条消息、{current_chars} 个字符）。"
                f"请用 /{over_cmd} 处理或 /{cancel_cmd} 放弃。"
            )
        state.messages.append(BufferedMessage(incoming, media_urls, media_types))
        count, chars = _buffer_stats(state)

    # 每满一批（默认 10 条）回复一次确认，避免刷屏；不足一批不打扰。
    if _truthy_setting("ack_messages", True) and count % batch_size == 0:
        return _control_response(
            event,
            gateway,
            f"已缓冲 {count} 条消息，发送 /{over_cmd} 一起处理。",
        )
    return {"action": "skip", "reason": "manual-buffered"}


def register(ctx) -> None:
    global _ctx
    _ctx = ctx
    ctx.register_hook("pre_gateway_dispatch", _on_pre_gateway_dispatch)
