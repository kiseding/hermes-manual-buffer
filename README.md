# Hermes 手动消息缓冲插件

> 语言：[English](README.en.md) | **中文**

为 Hermes gateway 提供手动入站消息缓冲：把连续发送的多条消息攒起来，再统一交给模型处理。

## 命令

- `/begin`：开始积攒当前聊天/会话的普通消息（提示语已本地化为中文）
- `/over`：把积攒的消息分批交给 Hermes 处理（每批最多 `flush_batch_size` 条，默认 10 条）
- `/cancel`：丢弃当前缓冲区
- `/preview`：查看当前缓冲区的内容预览

确认提示：每收集满一批（默认 10 条）回复一次“已缓冲 N 条”，不足一批不打扰；达到上限会提示处理或放弃。

积攒期间，其他 `/xxx` 命令照常可用，不受影响。

## 批量处理

- `/over` 会把缓冲中的内容合并成批量 prompt 提交给 Hermes。
- 图片/附件会携带在最终轮次的 property 上，供视觉/STT 等正常路由。
- 超过一批的剩余消息会继续留在缓冲区，并在稍后自动注入同一会话——**无需用户再次发送 `/over`**。
- 每次提交的批大小可通过 `flush_batch_size` 配置。

## 安装

把整个目录复制到：

```bash
~/.hermes/plugins/manual-buffer
```

然后启用并重启 gateway：

```bash
hermes plugins enable manual-buffer
systemctl --user restart hermes-gateway.service
```

## 配置

可选配置位于 `plugins.entries.manual-buffer.settings`：

```yaml
plugins:
  enabled:
    - manual-buffer
  entries:
    manual-buffer:
      settings:
        begin_command: begin
        over_command: over
        cancel_command: cancel
        preview_command: preview
        max_messages: 100
        max_chars: 50000
        flush_batch_size: 10
        ack_messages: true
```

## 说明

- 缓冲区存放在内存中；若在 `/over` 之前 gateway 重启，数据会丢失。
- 图片等附件按路径保留，只要 gateway 进程存活即可随批次转发。
- 插件使用官方 `pre_gateway_dispatch` hook，不改动 Hermes 核心；唯一的核心补丁是在 `run.py` 中给插件消息注入透传 `media_urls`/`media_types`。
- 选择 `/begin` 而非 `/start` 可避免与各平台的保留命令冲突。