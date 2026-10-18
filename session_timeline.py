"""Read a bounded, privacy-safe event timeline from one local session log.

This is an on-demand view over source files. It deliberately does not alter the
durable aggregate ledger: archived sessions keep their daily detail and stored
compactions, while a missing source log simply has no minute-level timeline.
"""

import datetime as _dt
import json
import os
import re

if __package__:
    from .parser import (
        FAST_SUFFIX, US_SUFFIX, _CODEX_SKIP, _buckets, _first_text, _from_iso,
        _is_subagent_path, normalize_claude, normalize_codex,
    )
else:
    from parser import (
        FAST_SUFFIX, US_SUFFIX, _CODEX_SKIP, _buckets, _first_text, _from_iso,
        _is_subagent_path, normalize_claude, normalize_codex,
    )


MAX_BYTES = 64 * 1024 * 1024
_SOURCES = {"claude", "claude-desktop", "codex"}
_NOT_TYPED = ("<local-command-caveat>", "<command-name>", "<command-message>",
              "<local-command-stdout>", "<task-notification>", "[Request interrupted by user")
_SLASH_COMMAND = re.compile(r"/[a-z][\w:.-]*(?:\s|$)")


def _timestamp(value):
    if not isinstance(value, str) or not value or len(value) > 80:
        return None
    try:
        _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    return value


def _timestamp_order(event):
    try:
        value = _dt.datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=_dt.timezone.utc)
        return value.timestamp()
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return 0


def _number(value):
    # bool is an int subclass but is never a meaningful token count.
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _claude_usage(values):
    if not isinstance(values, dict):
        return {}
    result = {}
    for source, target in (("input_tokens", "input"), ("output_tokens", "output"),
                           ("cache_read_input_tokens", "cache_read"),
                           ("cache_creation_input_tokens", "cache_write")):
        value = _number(values.get(source))
        if value is not None:
            result[target] = value
    details = values.get("output_tokens_details") if isinstance(values.get("output_tokens_details"), dict) else {}
    reasoning = _number(details.get("thinking_tokens"))
    if reasoning is not None:
        result["reasoning"] = reasoning
    server = values.get("server_tool_use") if isinstance(values.get("server_tool_use"), dict) else {}
    searches = _number(server.get("web_search_requests"))
    if searches is not None:
        result["web_search"] = searches
    return result


def _codex_usage(values):
    """Convert Codex's full-context usage into the parser's additive fields."""
    if not isinstance(values, dict):
        return {}
    raw_input = _number(values.get("input_tokens"))
    cached = _number(values.get("cached_input_tokens"))
    written = _number(values.get("cache_write_input_tokens"))
    output = _number(values.get("output_tokens"))
    reasoning = _number(values.get("reasoning_output_tokens"))
    result = {}
    if raw_input is not None:
        result["input"] = max(0, raw_input - (cached or 0) - (written or 0))
    if cached is not None:
        result["cache_read"] = cached
    if written is not None and raw_input is not None:
        result["cache_write"] = min(written, max(0, raw_input - (cached or 0)))
    if output is not None:
        result["output"] = output
    if reasoning is not None:
        result["reasoning"] = reasoning
    return result


def _text(value, limit=120):
    return value[:limit] if isinstance(value, str) and value else None


def _event(timestamp, kind, **fields):
    event = {"timestamp": timestamp, "kind": kind}
    event.update({key: value for key, value in fields.items() if value is not None})
    return event


def _event_date(timestamp):
    parsed = _from_iso(timestamp)
    return _buckets(parsed)[0] if parsed else None


def _in_range(timestamp, date_from=None, date_to=None):
    day = _event_date(timestamp)
    if day is None:
        return False
    return (not date_from or day >= date_from) and (not date_to or day <= date_to)


def _add(events, event, limit, date_from=None, date_to=None):
    if _in_range(event.get("timestamp"), date_from, date_to) and len(events) < limit:
        events.append(event)


def _claude_model(message):
    model = normalize_claude(message.get("model"))
    usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
    if usage.get("speed") == "fast":
        model += FAST_SUFFIX
    if str(usage.get("inference_geo") or "").lower() == "us":
        model += US_SUFFIX
    return model


def _claude_events(lines, session_id, event_limit, date_from, date_to, subagent_path):
    events = []
    # Claude repeats the complete response usage on each content-block record.
    # Keep the parser's eight-response window and max-watermark behavior, then
    # expose one request event with the final observed usage.
    responses = {}
    response_order = []
    seen_uuids = set()
    for line in lines:
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue
        sid = obj.get("sessionId") or obj.get("session_id")
        if sid and str(sid) != session_id:
            continue
        if obj.get("isSidechain") and not subagent_path:
            continue
        uid = obj.get("uuid")
        if uid:
            key = (str(uid).replace("-", "") + "0" * 12)[:12]
            if key in seen_uuids:
                continue
            seen_uuids.add(key)
        timestamp = _timestamp(obj.get("timestamp"))
        if not timestamp:
            continue
        msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
        typ = obj.get("type")
        if typ == "user" and not obj.get("isMeta") and not obj.get("isCompactSummary"):
            origin = obj.get("origin")
            content = msg.get("content")
            has_tool_result = isinstance(content, list) and any(
                isinstance(block, dict) and block.get("type") == "tool_result" for block in content)
            opening = _first_text(content).lstrip()
            if (opening and not has_tool_result
                    and (not isinstance(origin, dict) or origin.get("kind", "human") == "human")
                    and not opening.startswith(_NOT_TYPED) and not _SLASH_COMMAND.match(opening)):
                _add(events, _event(timestamp, "user_turn"), event_limit, date_from, date_to)
        if typ == "assistant" and msg.get("model") != "<synthetic>":
            usage = _claude_usage(msg.get("usage"))
            mid = msg.get("id")
            request_id = obj.get("requestId")
            if usage and mid:
                key = (str(mid), str(request_id or ""))
                previous = responses.get(key)
                if previous is None:
                    previous = _event(timestamp, "request", model=_text(_claude_model(msg)))
                    responses[key] = previous
                if key in response_order:
                    response_order.remove(key)
                response_order.append(key)
                previous["timestamp"] = timestamp
                previous["model"] = _text(_claude_model(msg)) or previous.get("model")
                for name, value in usage.items():
                    previous[name] = max(value, previous.get(name, 0))
                while len(response_order) > 8:
                    old = responses.pop(response_order.pop(0), None)
                    if old:
                        _add(events, old, event_limit, date_from, date_to)
            elif usage:
                # Match parser behavior when message.id is absent: do not guess a
                # cross-record identity for repeated blocks.
                _add(events, _event(timestamp, "request", model=_text(_claude_model(msg)), **usage),
                     event_limit, date_from, date_to)
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    name = _text(block.get("name"), 100)
                    if name:
                        _add(events, _event(timestamp, "tool", label=name), event_limit, date_from, date_to)
        elif typ == "attachment":
            attachment = obj.get("attachment") if isinstance(obj.get("attachment"), dict) else {}
            if attachment.get("type") == "queued_command" and attachment.get("commandMode") == "prompt":
                text = _first_text(attachment.get("prompt")).strip()
                if text and not text.startswith(("<ide_", "<system-reminder")) and not _SLASH_COMMAND.match(text):
                    _add(events, _event(timestamp, "user_turn"), event_limit, date_from, date_to)
    for response in responses.values():
        _add(events, response, event_limit, date_from, date_to)
    return events


def _codex_events(lines, session_id, event_limit, date_from, date_to):
    events = []
    current_model = None
    replay_last = None
    replay_initialized = False
    recent_counts = []
    use_usage_records = False
    identity_mismatch = False
    for line in lines:
        skip = any(marker in line for marker in _CODEX_SKIP)
        if skip and '"compacted"' not in line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue
        if skip and obj.get("type") != "compacted":
            continue
        timestamp = _timestamp(obj.get("timestamp"))
        typ = obj.get("type")
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        payload_type = payload.get("type")

        if typ == "session_meta":
            identity = payload.get("id") or payload.get("session_id")
            if identity:
                if str(identity) != session_id:
                    identity_mismatch = True
            if payload.get("forked_from_id") and timestamp and not replay_initialized:
                replay_last = timestamp
                replay_initialized = True

        if replay_last and timestamp:
            try:
                now = _dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                then = _dt.datetime.fromisoformat(replay_last.replace("Z", "+00:00"))
                gap = (now - then).total_seconds()
            except (TypeError, ValueError, OverflowError):
                gap = 0
            if gap <= 1.0:
                if not skip:
                    replay_last = timestamp
                if typ != "turn_context":
                    continue
            else:
                if not skip:
                    replay_last = None

        if typ == "token_usage_record":
            # Only an event that survives Codex's replay guard changes the
            # parser's source precedence from token_count to usage records.
            use_usage_records = True
        if not timestamp:
            continue
        if typ == "turn_context":
            if payload.get("model"):
                current_model = _text(normalize_codex(payload["model"])) or current_model
        elif typ == "event_msg" and payload_type == "user_message":
            if current_model != "codex-auto-review":
                _add(events, _event(timestamp, "user_turn"), event_limit, date_from, date_to)
        elif typ == "event_msg" and payload_type == "item_completed":
            item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
            if item.get("type") == "UserMessage" and current_model != "codex-auto-review":
                _add(events, _event(timestamp, "user_turn"), event_limit, date_from, date_to)
            elif item.get("type") == "SubAgentActivity" and item.get("kind") == "started":
                # This marker is on the parent rollout itself; it does not infer
                # a child identity or read another file.
                _add(events, _event(timestamp, "subagent", label="Subagent started"),
                     event_limit, date_from, date_to)
        elif typ == "token_usage_record":
            raw_usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
            usage = _codex_usage(raw_usage)
            # Codex writes one usage record per model response before its
            # token_count twin. Zero-usage compaction records are not requests.
            if (_number(raw_usage.get("input_tokens")) or _number(raw_usage.get("output_tokens"))):
                _add(events, _event(timestamp, "request", model=current_model, **usage),
                     event_limit, date_from, date_to)
        elif typ == "event_msg" and payload_type == "token_count" and not use_usage_records:
            info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
            last = info.get("last_token_usage") if isinstance(info.get("last_token_usage"), dict) else {}
            total = info.get("total_token_usage") if isinstance(info.get("total_token_usage"), dict) else {}
            signature = [total.get("input_tokens"), total.get("output_tokens"),
                         last.get("input_tokens"), last.get("output_tokens")]
            last_input = _number(last.get("input_tokens"))
            last_output = _number(last.get("output_tokens"))
            if (last_input or last_output) and not (total and signature in recent_counts):
                recent_counts.append(signature)
                del recent_counts[:-32]
                _add(events, _event(timestamp, "request", model=current_model, **_codex_usage(last)),
                     event_limit, date_from, date_to)
        elif typ == "response_item":
            if payload_type in ("function_call", "custom_tool_call"):
                name = _text(payload.get("name"), 100)
                if name:
                    _add(events, _event(timestamp, "tool", label=name, model=current_model),
                         event_limit, date_from, date_to)
            elif payload_type in ("web_search_call", "tool_search_call", "image_generation_call"):
                name = {"web_search_call": "web_search", "tool_search_call": "tool_search",
                        "image_generation_call": "image_generation"}[payload_type]
                _add(events, _event(timestamp, "tool", label=name, model=current_model),
                     event_limit, date_from, date_to)
    # If the file has no session_meta identity, root's exact path/session lookup
    # remains the source of identity. A conflicting full ID invalidates the file.
    return None if identity_mismatch else events


def timeline(source, path, session_id, max_events=400, date_from=None, date_to=None):
    """Return bounded event detail for one recognized local Claude/Codex log.

    `path` and the full `session_id` must come from the caller's validated
    ledger lookup; neither is ever included in the response. Optional date
    bounds filter returned events by the machine-local date while parsing still
    scans earlier records to establish deduplication and model state. No source text,
    prompts, tool arguments, filesystem paths, credentials, or compaction events
    are returned.
    """
    result = {"events": [], "truncated": False, "available": False, "detail": "unavailable"}
    if source not in _SOURCES or not isinstance(session_id, str) or not session_id or len(session_id) > 512:
        return result
    if not isinstance(path, (str, os.PathLike)):
        return result
    try:
        size = os.path.getsize(path)
        if not os.path.isfile(path):
            return result
    except OSError:
        return result

    limited = size > MAX_BYTES
    try:
        cap = max(0, min(int(max_events), 2000))
    except (TypeError, ValueError, OverflowError):
        cap = 400
    read_state = {"truncated": limited}

    def source_lines():
        consumed = 0
        with open(path, "rb") as handle:
            while consumed < MAX_BYTES:
                remaining = MAX_BYTES - consumed
                raw_line = handle.readline(remaining + 1)
                if not raw_line:
                    return
                if len(raw_line) > remaining:
                    read_state["truncated"] = True
                    return
                consumed += len(raw_line)
                yield raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if handle.read(1):
                read_state["truncated"] = True

    for name, value in (("date_from", date_from), ("date_to", date_to)):
        if value is not None:
            try:
                if not isinstance(value, str) or len(value) != 10:
                    raise ValueError
                _dt.date.fromisoformat(value)
            except (TypeError, ValueError):
                if name == "date_from":
                    date_from = None
                else:
                    date_to = None

    if source == "codex":
        try:
            events = _codex_events(source_lines(), session_id, cap + 1, date_from, date_to)
        except OSError:
            return result
    else:
        try:
            events = _claude_events(source_lines(), session_id, cap + 1, date_from, date_to,
                                    _is_subagent_path(path))
        except OSError:
            return result
    if events is None:
        return result

    events.sort(key=_timestamp_order)
    result["truncated"] = read_state["truncated"] or len(events) > cap
    if len(events) > cap:
        events = events[:cap]
    result.update(events=events, available=True,
                  detail="partial" if result["truncated"] else "source log")
    return result
