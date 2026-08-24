# Hermes Manual Buffer

Manual inbound message buffering for Hermes gateway.

Use this when you want to send many consecutive messages and have Hermes process them as one combined request.
Text plus media attachments are buffered together. On each `/over`, at most `flush_batch_size` messages are submitted to Hermes; the remaining buffers are automatically injected into the same session without further user input.

## Commands

- `/begin` starts buffering normal messages in the current chat/session.
- `/over` starts processing the buffered messages in batches of up to `flush_batch_size` (default 10). Remaining messages stay buffered and are automatically injected into the same session afterwards — no need to type `/over` again.
- `/cancel` discards the current buffer.
- `/preview` shows a short preview of the current buffer.

Slash commands other than these continue to pass through while buffering is active.

## Install

Copy this directory to:

```bash
~/.hermes/plugins/manual-buffer
```

Then enable it:

```bash
hermes plugins enable manual-buffer
systemctl --user restart hermes-gateway.service
```

## Configuration

Optional settings live under `plugins.entries.manual-buffer.settings`:

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

## Notes

- Buffer state is in memory and is lost if the gateway restarts before `/over`.
- Images and other attachments are preserved by path while the gateway process stays alive.
- `/over` processes at most `flush_batch_size` buffered messages at a time; the remaining batches are auto-injected to avoid overloading the model.
- The plugin uses Hermes' `pre_gateway_dispatch` hook and does not patch Hermes core.
- `/begin` is used instead of `/start` to avoid platform-reserved command conflicts.
