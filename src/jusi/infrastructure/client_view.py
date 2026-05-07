from __future__ import annotations

import json


def build_client_view(
    *,
    client_id: str,
    session_id: str,
    client_bufnr: int,
    active_cell_id: int | None,
    execution_status: str,
    transcript: list[dict],
) -> dict:
    status = execution_status or "idle"
    if active_cell_id is None:
        title = f"client {client_id}: {status}"
    else:
        title = f"cell {active_cell_id}: {status}"

    bound = str(client_bufnr) if client_bufnr >= 0 else "unbound"
    lines: list[str] = [f"meta> client={client_id} session={session_id} bufnr={bound}"]
    rendered_blocks: list[tuple[str | None, list[str]]] = []
    display_block_positions: dict[str, int] = {}
    clear_pending = False
    for event in transcript[-20:]:
        event_type = str(event.get("type", "")).strip() or "event"
        if event_type == "clear_output":
            wait = bool(event.get("wait", False))
            if wait:
                clear_pending = True
            else:
                rendered_blocks = []
                display_block_positions = {}
                clear_pending = False
            continue
        key, event_lines = _render_event_lines(event)
        if not event_lines:
            continue
        if clear_pending:
            rendered_blocks = []
            display_block_positions = {}
            clear_pending = False
        if key is not None and key in display_block_positions:
            rendered_blocks[display_block_positions[key]] = (key, event_lines)
            continue
        if key is not None:
            display_block_positions[key] = len(rendered_blocks)
        rendered_blocks.append((key, event_lines))
    for _key, event_lines in rendered_blocks:
        lines.extend(event_lines)

    if len(lines) == 1:
        if client_bufnr >= 0:
            lines.append(f"status> {status}")
        else:
            lines.append("waiting for transcript")
    if execution_status == "interrupted" and lines[-1] != "interrupted":
        lines.append("interrupted")

    return {
        "title": title,
        "lines": lines,
        "execution_status": execution_status,
        "active_cell_id": active_cell_id,
    }


def build_client_terminal_lines(
    *,
    execution_status: str,
    transcript: list[dict],
) -> list[str]:
    rendered_blocks: list[tuple[str | None, list[str]]] = []
    display_block_positions: dict[str, int] = {}
    clear_pending = False
    for event in transcript[-200:]:
        event_type = str(event.get("type", "")).strip() or "event"
        if event_type == "clear_output":
            wait = bool(event.get("wait", False))
            if wait:
                clear_pending = True
            else:
                rendered_blocks = []
                display_block_positions = {}
                clear_pending = False
            continue
        key, event_lines = _render_terminal_event_lines(event)
        if not event_lines:
            continue
        if clear_pending:
            rendered_blocks = []
            display_block_positions = {}
            clear_pending = False
        if key is not None and key in display_block_positions:
            rendered_blocks[display_block_positions[key]] = (key, event_lines)
            continue
        if key is not None:
            display_block_positions[key] = len(rendered_blocks)
        rendered_blocks.append((key, event_lines))
    lines: list[str] = []
    for _key, event_lines in rendered_blocks:
        lines.extend(event_lines)
    if not lines:
        if execution_status == "busy":
            return ["running..."]
        if execution_status == "interrupted":
            return ["interrupted"]
        return []
    return lines


def _render_event_lines(event: dict) -> tuple[str | None, list[str]]:
    event_type = str(event.get("type", "")).strip() or "event"
    if event_type == "execution_started":
        kind = str(event.get("kind", "")).strip()
        syntax = str(event.get("syntax", "")).strip()
        details = f"{kind}:{syntax}" if kind or syntax else ""
        return None, [f"started cell {event.get('cell_id', '?')} [{details}]"]
    if event_type == "execution_finished":
        return None, [f"finished: {event.get('status', '')}"]
    if event_type == "execution_interrupted":
        return None, ["interrupted"]
    if event_type == "execution_state":
        return None, [f"state: {event.get('status', '')}"]
    if event_type == "handler_handoff":
        handler_id = str(event.get("handler_id", "")).strip() or "handler"
        magic_name = str(event.get("magic_name", "")).strip() or "magic"
        lines = [f"handler.handoff> magic={magic_name} handler={handler_id}"]
        meta = event.get("meta", {})
        if meta:
            lines.append(f"handler.meta> {json.dumps(meta, ensure_ascii=True, sort_keys=True)}")
        return None, lines
    if event_type == "handler_notice":
        text = str(event.get("text", "")).strip()
        return None, [f"handler.notice> {text}"] if text else []
    if event_type == "handler_stream":
        text = str(event.get("text", ""))
        return None, [f"handler.out> {text}"] if text else ["handler.out>"]
    if event_type == "handler_input":
        text = str(event.get("text", ""))
        return None, [f"handler.in> {text}"] if text else ["handler.in>"]
    if event_type == "handler_prompt":
        text = str(event.get("text", ""))
        return None, [f"handler.prompt> {text}"] if text else ["handler.prompt>"]
    if event_type == "handler_channel_event":
        return None, _render_handler_channel_event(event)
    if event_type == "frontend_action_request":
        return None, _render_frontend_action_request(event)
    if event_type == "execute_input":
        execution_count = event.get("execution_count")
        prefix = "execute"
        if execution_count not in (None, ""):
            prefix = f"execute[{execution_count}]"
        code = str(event.get("code", ""))
        lines: list[str] = []
        for chunk in code.splitlines() or [code]:
            text = chunk.rstrip("\n")
            if text:
                lines.append(f"{prefix}> {text}")
        return None, lines or [f"{prefix}>"]
    if event_type == "input_request":
        prompt = str(event.get("prompt", ""))
        prefix = "password" if bool(event.get("password", False)) else "input"
        if prompt:
            return None, [f"{prefix}> {prompt}"]
        return None, [f"{prefix}>"]
    if event_type == "stream":
        name = str(event.get("name", "")).strip() or "stream"
        raw_text = str(event.get("text", ""))
        lines: list[str] = []
        for chunk in raw_text.splitlines() or [raw_text]:
            text = chunk.rstrip("\n")
            if text:
                lines.append(f"{name}> {text}")
        return None, lines
    if event_type == "error":
        lines = [f"error: {event.get('ename', '')}: {event.get('evalue', '')}"]
        for frame in list(event.get("traceback", [])):
            text = str(frame).strip()
            if text:
                lines.append(f"trace> {text}")
        return None, lines
    if event_type == "display_data":
        return _display_block(event, "display")
    if event_type == "update_display_data":
        return _display_block(event, "display")
    if event_type == "execute_result":
        return None, _render_data_lines("result", event.get("data", {}))
    if event_type in {"comm_open", "comm_msg", "comm_close"}:
        return None, _render_comm_lines(event_type, event)
    return None, [json.dumps(event, ensure_ascii=True, sort_keys=True)]


def _render_terminal_event_lines(event: dict) -> tuple[str | None, list[str]]:
    event_type = str(event.get("type", "")).strip() or "event"
    if event_type in {"execution_started", "execution_finished", "execution_state", "execute_input"}:
        return None, []
    if event_type == "execution_interrupted":
        return None, ["interrupted"]
    if event_type == "input_request":
        prompt = str(event.get("prompt", ""))
        prefix = "password input required" if bool(event.get("password", False)) else "input required"
        if prompt:
            return None, [f"{prefix}: {prompt}"]
        return None, [prefix]
    if event_type == "stream":
        return None, _render_stream_lines(str(event.get("text", "")))
    if event_type == "error":
        lines = []
        for frame in list(event.get("traceback", [])):
            text = str(frame).rstrip("\n")
            if text:
                lines.extend(text.splitlines())
        if not lines:
            header = ": ".join(part for part in [str(event.get("ename", "")).strip(), str(event.get("evalue", "")).strip()] if part)
            if header:
                lines.append(header)
        return None, lines
    if event_type == "display_data":
        return _terminal_display_block(event)
    if event_type == "update_display_data":
        return _terminal_display_block(event)
    if event_type == "execute_result":
        return None, _render_terminal_data_lines(event.get("data", {}))
    return None, []


def _display_block(event: dict, prefix: str) -> tuple[str | None, list[str]]:
    display_id = str(event.get("display_id", "")).strip() or None
    return display_id, _render_data_lines(prefix, event.get("data", {}))


def _render_data_lines(prefix: str, data: object) -> list[str]:
    if not isinstance(data, dict):
        return [f"{prefix}> {json.dumps(data, ensure_ascii=True, sort_keys=True)}"]
    text_plain = data.get("text/plain")
    if text_plain is not None:
        raw_text = str(text_plain)
        lines: list[str] = []
        for chunk in raw_text.splitlines() or [raw_text]:
            text = chunk.rstrip("\n")
            if text:
                lines.append(f"{prefix}> {text}")
        if lines:
            return lines
    return [f"{prefix}> {json.dumps(data, ensure_ascii=True, sort_keys=True)}"]


def _render_terminal_data_lines(data: object) -> list[str]:
    if not isinstance(data, dict):
        return [json.dumps(data, ensure_ascii=True, sort_keys=True)]
    text_plain = data.get("text/plain")
    if text_plain is not None:
        return _render_stream_lines(str(text_plain))
    return [json.dumps(data, ensure_ascii=True, sort_keys=True)]


def _terminal_display_block(event: dict) -> tuple[str | None, list[str]]:
    display_id = str(event.get("display_id", "")).strip() or None
    return display_id, _render_terminal_data_lines(event.get("data", {}))


def _render_stream_lines(raw_text: str) -> list[str]:
    lines: list[str] = []
    for chunk in raw_text.splitlines() or [raw_text]:
        text = chunk.rstrip("\n")
        if text:
            lines.append(text)
    return lines


def _render_comm_lines(event_type: str, event: dict) -> list[str]:
    comm_id = str(event.get("comm_id", "")).strip()
    target_name = str(event.get("target_name", "")).strip()
    parts = [event_type]
    if comm_id:
        parts.append(f"comm_id={comm_id}")
    if target_name:
        parts.append(f"target={target_name}")

    lines = [" ".join(parts)]
    if "data" in event:
        lines.append(f"comm.data> {json.dumps(event.get('data', {}), ensure_ascii=True, sort_keys=True)}")
    if "state" in event:
        lines.append(f"comm.state> {json.dumps(event.get('state', {}), ensure_ascii=True, sort_keys=True)}")
    return lines


def _render_handler_channel_event(event: dict) -> list[str]:
    event_type = str(event.get("event_type", "")).strip() or "event"
    payload = event.get("payload", {})
    if event_type == "handler_snapshot" and isinstance(payload, dict):
        handler_id = str(payload.get("handler_id", "")).strip() or "handler"
        mode = str(payload.get("mode", "")).strip() or "unknown"
        entry = str(payload.get("entry", "")).strip()
        lines = [f"handler> {handler_id} mode={mode}"]
        if entry:
            lines.append(f"handler.entry> {entry}")
        return lines
    return [f"handler.event> {event_type} {json.dumps(payload, ensure_ascii=True, sort_keys=True)}"]


def _render_frontend_action_request(event: dict) -> list[str]:
    action_type = str(event.get("action_type", "")).strip() or "action"
    payload = event.get("payload", {})
    lines = [f"frontend.action> {action_type}"]
    if payload:
        lines.append(f"frontend.payload> {json.dumps(payload, ensure_ascii=True, sort_keys=True)}")
    return lines
