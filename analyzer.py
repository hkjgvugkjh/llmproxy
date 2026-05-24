"""Offline log analysis tool for LLM Proxy."""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


def find_log_files(paths: Optional[list[str]] = None, log_dir: str = "logs") -> list[Path]:
    if paths:
        return sorted(Path(p) for p in paths if Path(p).exists())
    return sorted(Path(log_dir).glob("*.log"))


def parse_streaming_chunks(raw_body: str) -> dict:
    content_parts = []
    tool_calls: dict[int, dict] = {}
    usage = None
    model = None
    finish_reason = None
    for line in raw_body.strip().splitlines():
        line = line.strip()
        if not line or line == "data: [DONE]":
            continue
        if not line.startswith("data: "):
            continue
        try:
            chunk = json.loads(line[6:])
        except json.JSONDecodeError:
            continue
        if not chunk:
            continue
        if chunk.get("model"):
            model = chunk.get("model")
        if chunk.get("lastOne"):
            usage = chunk.get("usage")
        for choice in chunk.get("choices", []):
            delta = choice.get("delta", {})
            c = delta.get("content")
            if c:
                content_parts.append(c)
            for tc in delta.get("tool_calls", []):
                idx = tc.get("index", 0)
                if idx not in tool_calls:
                    tool_calls[idx] = {"id": "", "type": "function",
                                       "function": {"name": "", "arguments": ""}}
                if tc.get("id"):
                    tool_calls[idx]["id"] = tc["id"]
                if tc.get("type"):
                    tool_calls[idx]["type"] = tc["type"]
                fn = tc.get("function", {})
                if fn.get("name"):
                    tool_calls[idx]["function"]["name"] = fn["name"]
                if fn.get("arguments"):
                    tool_calls[idx]["function"]["arguments"] += fn["arguments"]
            fr = choice.get("finish_reason")
            if fr:
                finish_reason = fr
    merged_tc = [tool_calls[i] for i in sorted(tool_calls.keys())] if tool_calls else None
    result = {"content": "".join(content_parts), "tool_calls": merged_tc,
              "finish_reason": finish_reason, "model": model}
    if usage:
        result["usage"] = usage
    return result


def extract_user_messages(body_str: str) -> list[dict]:
    try:
        data = json.loads(body_str)
    except json.JSONDecodeError:
        return []
    messages = data.get("messages", [])
    result = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if isinstance(content, list):
            text_parts = [item.get("text", "") for item in content
                          if isinstance(item, dict) and item.get("type") == "text"]
            content = "\n".join(text_parts)
        result.append({"role": role, "content": content})
    return result


def parse_log_file(filepath: Path, no_system: bool = False,
                   only_user: bool = False) -> list[dict]:
    records = []
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    pattern = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})  INFO  (>>>|<<<) (\w+)")
    matches = list(pattern.finditer(text))
    for i, m in enumerate(matches):
        timestamp = m.group(1)
        direction = m.group(2)
        record_type = m.group(3)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[start:end].strip()
        if direction == ">>>":
            body_match = re.match(r"\s*MODEL=(\S+)\s+BODY=(.*)", block, re.DOTALL)
            if not body_match:
                continue
            model = body_match.group(1)
            body_str = body_match.group(2).strip()
            messages = extract_user_messages(body_str)
            user_contents = []
            for msg in messages:
                if msg["role"] == "user":
                    user_contents.append(msg["content"])
                elif msg["role"] == "system" and not no_system:
                    content = msg["content"]
                    if len(content) > 200 and only_user:
                        continue
                    user_contents.append(f"[SYSTEM] {content[:500]}..." if len(content) > 500 else f"[SYSTEM] {content}")
            if only_user:
                user_contents = [c for c in user_contents if not c.startswith("[SYSTEM]")]
            if not user_contents and only_user:
                continue
            records.append({
                "timestamp": timestamp, "model": model, "direction": "input",
                "user_input": "\n".join(user_contents) if user_contents else "(no user message)",
                "assistant_output": None, "tool_calls": None,
                "finish_reason": None, "usage": None, "status": None,
            })
        elif direction == "<<<":
            body_match = re.match(r"\s*MODEL=(\S+)\s+STATUS=(\d+)\s+BODY=(.*)", block, re.DOTALL)
            if not body_match:
                error_match = re.match(r"\s*MODEL=(\S+)\s+REASON=(.*)", block, re.DOTALL)
                if error_match:
                    records.append({
                        "timestamp": timestamp, "model": error_match.group(1),
                        "direction": "error", "user_input": None,
                        "assistant_output": None, "tool_calls": None,
                        "finish_reason": "error", "usage": None,
                        "status": "error", "error_reason": error_match.group(2).strip(),
                    })
                continue
            model = body_match.group(1)
            status = int(body_match.group(2))
            body_str = body_match.group(3).strip()
            if body_str.startswith("data:"):
                parsed = parse_streaming_chunks(body_str)
                assistant_output = parsed["content"]
                tool_calls = parsed.get("tool_calls")
                finish_reason = parsed.get("finish_reason")
                usage = parsed.get("usage")
            else:
                try:
                    data = json.loads(body_str)
                    assistant_output = ""
                    tool_calls = None
                    finish_reason = None
                    for choice in data.get("choices", []):
                        msg = choice.get("message", {})
                        if msg.get("content"):
                            assistant_output += msg["content"]
                        if msg.get("tool_calls"):
                            tool_calls = msg["tool_calls"]
                        if choice.get("finish_reason"):
                            finish_reason = choice["finish_reason"]
                    usage = data.get("usage")
                except json.JSONDecodeError:
                    assistant_output = body_str[:500]
                    tool_calls = None
                    finish_reason = None
                    usage = None
            records.append({
                "timestamp": timestamp, "model": model, "direction": "output",
                "user_input": None, "assistant_output": assistant_output,
                "tool_calls": tool_calls, "finish_reason": finish_reason,
                "usage": usage, "status": status,
            })
    return records


def pair_conversations(records: list[dict]) -> list[dict]:
    conversations = []
    current_input = None
    for rec in records:
        if rec["direction"] == "input":
            current_input = rec
        elif rec["direction"] == "output" and current_input:
            conversations.append({
                "timestamp": current_input["timestamp"],
                "model": current_input["model"],
                "user_input": current_input["user_input"],
                "assistant_output": rec["assistant_output"],
                "tool_calls": rec["tool_calls"],
                "finish_reason": rec["finish_reason"],
                "usage": rec["usage"],
                "status": rec["status"],
            })
            current_input = None
        elif rec["direction"] == "error" and current_input:
            conversations.append({
                "timestamp": current_input["timestamp"],
                "model": current_input["model"],
                "user_input": current_input["user_input"],
                "assistant_output": None, "tool_calls": None,
                "finish_reason": "error", "usage": None,
                "status": "error", "error_reason": rec.get("error_reason", ""),
            })
            current_input = None
    return conversations


def main():
    import argparse
    parser = argparse.ArgumentParser(description="LLM Proxy log analyzer")
    parser.add_argument("files", nargs="*", help="Log file paths")
    parser.add_argument("--all", action="store_true", help="Analyze all logs in logs/ dir")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--output", "-o", help="Output file path")
    parser.add_argument("--no-system", action="store_true", help="Skip system messages")
    parser.add_argument("--only-user", action="store_true", help="Only user role input")
    parser.add_argument("--stats", action="store_true", help="Only output statistics")
    args = parser.parse_args()

    if args.files:
        log_files = find_log_files(args.files)
    else:
        log_files = find_log_files()

    if not log_files:
        print("No log files found", file=sys.stderr)
        sys.exit(1)

    all_conversations = []
    for log_file in log_files:
        records = parse_log_file(log_file, no_system=args.no_system, only_user=args.only_user)
        conversations = pair_conversations(records)
        all_conversations.extend(conversations)

    if args.stats:
        total = len(all_conversations)
        errors = sum(1 for c in all_conversations if c.get("status") == "error")
        total_input_tokens = sum((c.get("usage") or {}).get("prompt_tokens", 0) or 0 for c in all_conversations)
        total_output_tokens = sum((c.get("usage") or {}).get("completion_tokens", 0) or 0 for c in all_conversations)
        models = {}
        for c in all_conversations:
            m = c.get("model", "unknown")
            models[m] = models.get(m, 0) + 1
        print(json.dumps({
            "total_conversations": total, "errors": errors,
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "models": models,
        }, ensure_ascii=False, indent=2))
        return

    if args.format == "json":
        report = json.dumps(all_conversations, ensure_ascii=False, indent=2)
    else:
        lines = ["=" * 80, "LLM Proxy Log Analysis Report",
                 f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                 f"Conversations: {len(all_conversations)}", "=" * 80]
        for i, conv in enumerate(all_conversations, 1):
            lines += ["", f"--- #{i} | {conv['timestamp']} | {conv['model']} ---", "",
                       f"[User]\n{conv['user_input'] or '(empty)'}", "",
                       f"[Assistant] finish={conv.get('finish_reason', 'N/A')}\n{conv['assistant_output'] or '(empty)'}"]
            if conv.get("tool_calls"):
                lines.append(f"\n[Tool Calls] {len(conv['tool_calls'])}")
                for j, tc in enumerate(conv["tool_calls"]):
                    fn = tc.get("function", {})
                    lines.append(f"  [{j+1}] {fn.get('name', '?')}({fn.get('arguments', '')[:200]})")
            if conv.get("usage"):
                u = conv["usage"]
                lines.append(f"\n[Token] prompt={u.get('prompt_tokens','?')} completion={u.get('completion_tokens','?')} total={u.get('total_tokens','?')}")
            lines.append("-" * 80)
        report = "\n".join(lines)

    if args.output:
        with open(args_output, "w", encoding="utf-8") as f:
            f.write(report)
    else:
        print(report)


if __name__ == "__main__":
    main()
