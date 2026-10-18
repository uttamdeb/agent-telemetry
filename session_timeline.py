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
        _is_subagent_path, normalize_claude, normalize_codex, _claude_effort, _sdk_model, _sdk_tokens,
    )
else:
    from parser import (
        FAST_SUFFIX, US_SUFFIX, _CODEX_SKIP, _buckets, _first_text, _from_iso,
        _is_subagent_path, normalize_claude, normalize_codex, _claude_effort, _sdk_model, _sdk_tokens,
    )


MAX_BYTES = 64 * 1024 * 1024
_SOURCES = {"claude", "claude-desktop", "codex", "copilot"}
_VIEWS = {"all", "activity", "requests", "tools", "compactions"}


class _Events(list):
    def __init__(self, view):
        super().__init__()
        self.kinds = {"activity": {"user_turn", "model", "reasoning", "subagent", "session", "error", "compaction"},
                      "requests": {"request"}, "tools": {"tool"}, "compactions": {"compaction"}}.get(view)


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
    if getattr(events, "kinds", None) is not None and event.get("kind") not in events.kinds:
        return
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


def _claude_events(lines, session_id, event_limit, date_from, date_to, subagent_path, view="all"):
    events = _Events(view)
    current_model, current_effort = None, None
    agent_tools = {}
    seen_agent_tools = set()
    agent_number = 0
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
        if typ == "system" and obj.get("subtype") == "api_error":
            _add(events, _event(timestamp, "error", label="Provider request error"), event_limit, date_from, date_to)
        if typ == "user" and isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                tool_id = block.get("tool_use_id")
                agent = agent_tools.pop(tool_id, None) if isinstance(tool_id, str) else None
                if agent:
                    number, background = agent
                    label = "Subagent request failed" if block.get("is_error") else "Subagent tool returned" if background else "Subagent completed"
                    _add(events, _event(timestamp, "subagent", label=label, agent=number), event_limit, date_from, date_to)
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
            if msg.get("model"):
                model = _text(_claude_model(msg))
                if model != current_model:
                    _add(events, _event(timestamp, "model", label="Recorded model", model=model), event_limit, date_from, date_to)
                    current_model = model
            effort, _ = _claude_effort(obj)
            if effort and effort != current_effort:
                _add(events, _event(timestamp, "reasoning", label="Recorded effort · " + effort, model=current_model), event_limit, date_from, date_to)
            current_effort = effort
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
                    if name in ("Agent", "Task"):
                        tool_id = block.get("id")
                        if isinstance(tool_id, str) and tool_id not in seen_agent_tools:
                            seen_agent_tools.add(tool_id)
                            agent_number += 1
                            inputs = block.get("input") if isinstance(block.get("input"), dict) else {}
                            agent_tools[tool_id] = (agent_number, inputs.get("run_in_background") is True)
                            _add(events, _event(timestamp, "subagent", label="Subagent requested", agent=agent_number), event_limit, date_from, date_to)
        elif typ == "attachment":
            attachment = obj.get("attachment") if isinstance(obj.get("attachment"), dict) else {}
            if attachment.get("type") == "queued_command" and attachment.get("commandMode") == "prompt":
                text = _first_text(attachment.get("prompt")).strip()
                if text and not text.startswith(("<ide_", "<system-reminder")) and not _SLASH_COMMAND.match(text):
                    _add(events, _event(timestamp, "user_turn"), event_limit, date_from, date_to)
    for response in responses.values():
        _add(events, response, event_limit, date_from, date_to)
    return events


def _codex_events(lines, session_id, event_limit, date_from, date_to, view="all"):
    events = _Events(view)
    current_model = None
    current_effort = None
    agents = {}
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
                model = _text(normalize_codex(payload["model"]))
                if model and model != current_model and not replay_last:
                    _add(events, _event(timestamp, "model", label="Recorded model context", model=model), event_limit, date_from, date_to)
                current_model = model or current_model
            effort = payload.get("effort")
            if effort in ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra") and effort != current_effort and not replay_last:
                _add(events, _event(timestamp, "reasoning", label="Recorded effort · " + effort, model=current_model), event_limit, date_from, date_to)
            current_effort = effort if isinstance(effort, str) else None
        elif typ == "event_msg" and payload_type in ("task_started", "task_complete", "turn_aborted"):
            label = {"task_started": "Turn started", "task_complete": "Turn completed", "turn_aborted": "Turn interrupted"}[payload_type]
            _add(events, _event(timestamp, "session", label=label), event_limit, date_from, date_to)
        elif typ == "event_msg" and payload_type == "user_message":
            if current_model != "codex-auto-review":
                _add(events, _event(timestamp, "user_turn"), event_limit, date_from, date_to)
        elif typ == "event_msg" and payload_type == "item_completed":
            item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
            if item.get("type") == "UserMessage" and current_model != "codex-auto-review":
                _add(events, _event(timestamp, "user_turn"), event_limit, date_from, date_to)
            elif item.get("type") == "SubAgentActivity" and item.get("kind") in ("started", "completed", "interacted", "closed", "failed"):
                # This marker is on the parent rollout itself; it does not infer
                # a child identity or read another file.
                aid = item.get("agent_thread_id")
                if isinstance(aid, str) and aid not in agents:
                    agents[aid] = len(agents) + 1
                label = {"started": "Subagent started", "completed": "Subagent completed", "interacted": "Subagent contacted", "closed": "Subagent closed", "failed": "Subagent failed"}[item["kind"]]
                _add(events, _event(timestamp, "subagent", label=label, agent=agents.get(aid) if isinstance(aid, str) else None),
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


def _copilot_events(lines, session_id, event_limit, date_from, date_to, view="all"):
    events = _Events(view)
    seen, users, tools, receipts = set(), set(), set(), {}
    agents, models, efforts = {}, {}, {}
    started = False

    def emit(timestamp, kind, aid=None, **fields):
        _add(events, _event(timestamp, kind, agent=agents.get(aid),
                           subagent=True if aid else None, **fields), event_limit, date_from, date_to)

    for line in lines:
        try:
            e = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(e, dict) or not isinstance(e.get("data"), dict):
            continue
        eid = e.get("id")
        if not isinstance(eid, str) or eid in seen:
            continue
        seen.add(eid)
        timestamp = _timestamp(e.get("timestamp"))
        if not timestamp:
            continue
        d, kind = e["data"], e.get("type")
        aid = e.get("agentId") if isinstance(e.get("agentId"), str) else None
        if kind == "session.start":
            if d.get("sessionId") != session_id:
                return None
            started = True
            emit(timestamp, "session", label="Session started")
        if not started:
            continue
        if kind == "subagent.started" and aid and aid not in agents:
            agents[aid] = len(agents) + 1
            emit(timestamp, "subagent", aid, label="Subagent started")
        elif kind in ("subagent.completed", "subagent.failed"):
            label = "Subagent failed" if kind.endswith("failed") else "Subagent cancelled" if d.get("cancelled") is True else "Subagent completed"
            emit(timestamp, "subagent", aid, label=label)
        elif kind in ("session.resume", "session.shutdown", "session.task_complete", "abort", "session.error"):
            labels = {"session.resume": "Session resumed", "session.shutdown": "Session stopped",
                      "session.task_complete": "Task completed", "abort": "Interrupted", "session.error": "SDK error recorded"}
            emit(timestamp, "error" if kind == "session.error" else "session", aid, label=labels[kind])
        raw_model = (d.get("selectedModel") if kind == "session.start" else
                     d.get("newModel") if kind == "session.model_change" else
                     d.get("model") if kind in ("subagent.started", "subagent.configured") else None)
        if isinstance(raw_model, str) and raw_model:
            model = _sdk_model(raw_model)
            if models.get(aid) != model:
                emit(timestamp, "model", aid, label="Recorded model", model=model)
                models[aid] = model
        effort = d.get("reasoningEffort")
        if kind in ("subagent.configured", "session.model_change") and effort in ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"):
            if efforts.get(aid) != effort:
                emit(timestamp, "reasoning", aid, label="Reasoning configured · " + effort, model=models.get(aid))
                efforts[aid] = effort
        if kind == "user.message":
            key = (aid, d.get("messageId") if isinstance(d.get("messageId"), str) else eid)
            if key not in users:
                users.add(key)
                emit(timestamp, "user_turn", aid)
        tool_rows = (d.get("toolRequests") or []) if kind == "assistant.message" else [d] if kind == "tool.execution_start" else []
        for tool in tool_rows if isinstance(tool_rows, list) else []:
            if not isinstance(tool, dict):
                continue
            tool_id, name = tool.get("toolCallId"), _text(tool.get("toolName") or tool.get("name"), 100)
            if isinstance(tool_id, str) and name and (aid, tool_id) not in tools:
                tools.add((aid, tool_id))
                emit(timestamp, "tool", aid, label=name, model=_sdk_model(d["model"]) if isinstance(d.get("model"), str) else models.get(aid))
        if kind in ("assistant.usage", "session.usage_record"):
            usage = d.get("usage") if kind == "session.usage_record" else d
            if not isinstance(usage, dict) or usage.get("interactionType") == "conversation-compaction":
                continue
            tokens = _sdk_tokens(usage)
            if tokens is None:
                continue
            accounting = usage.get("accounting") if isinstance(usage.get("accounting"), dict) else {}
            if not aid and accounting.get("sourceSessionId") not in (None, session_id):
                continue
            identity = usage.get("apiCallId") or accounting.get("usageId") or eid
            if not isinstance(identity, str):
                continue
            key = (aid, identity)
            old = receipts.get(key)
            if old is None or kind == "session.usage_record" or old[4] != "session.usage_record":
                receipts[key] = (old[0] if old else timestamp, aid, usage, tokens, kind)
        elif kind == "session.compaction_complete" and d.get("success") is True:
            trigger = d.get("trigger")
            emit(timestamp, "compaction", aid,
                 trigger=trigger if trigger in ("manual", "auto", "threshold", "context_limit_retry", "memory_pressure", "model_switch") else "unknown",
                 before=_number(d.get("preCompactionTokens")), after=_number(d.get("postCompactionTokens")))
    for timestamp, aid, usage, tokens, _ in receipts.values():
        emit(timestamp, "request", aid, model=_sdk_model(usage.get("model")),
             input=tokens["in"], output=tokens["out"],
             cache_read=tokens["cr"] if ("cacheReadTokens" in usage or "cachedTokens" in usage) else None,
             cache_write=tokens["cc"] if "cacheWriteTokens" in usage else None,
             reasoning=tokens["reason"] if "reasoningTokens" in usage else None)
    return events if started else None


def timeline(source, path, session_id, max_events=400, date_from=None, date_to=None, view="all"):
    """Return bounded event detail for one recognized local Claude/Codex/SDK log.

    `path` and the full `session_id` must come from the caller's validated
    ledger lookup; neither is ever included in the response. Optional date
    bounds filter returned events by the machine-local date while parsing still
    scans earlier records to establish deduplication and model state. No source text,
    prompts, tool arguments, filesystem paths, or credentials are returned.
    Event views filter before the cap, so tools cannot crowd out agent activity.
    """
    result = {"events": [], "truncated": False, "available": False, "detail": "unavailable"}
    if view not in _VIEWS:
        raise ValueError("Unknown timeline event view.")
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
            events = _codex_events(source_lines(), session_id, cap + 1, date_from, date_to, view)
        except OSError:
            return result
    elif source == "copilot":
        try:
            events = _copilot_events(source_lines(), session_id, cap + 1, date_from, date_to, view)
        except OSError:
            return result
    else:
        try:
            events = _claude_events(source_lines(), session_id, cap + 1, date_from, date_to,
                                    _is_subagent_path(path), view)
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
