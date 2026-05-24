#!/usr/bin/env python3
"""
LLM Proxy 日志分析工具
分析 logs/ 目录下的日志文件，提取用户输入和 LLM 输出。
流式输出自动合并为完整内容。

用法:
    python3 analyze_logs.py [log_file ...]          # 分析指定日志文件
    python3 analyze_logs.py --all                     # 分析所有日志
    python3 analyze_logs.py --format json             # JSON 格式输出
    python3 analyze_logs.py --format text             # 纯文本格式输出（默认）
    python3 analyze_logs.py --output result.txt       # 输出到文件
    python3 analyze_logs.py --no-system               # 跳过 system 消息
    python3 analyze_logs.py --only-user               # 只保留 user 角色输入
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

LOG_DIR = Path(__file__).parent / "logs"


def find_log_files(paths: list[str] | None = None) -> list[Path]:
    """获取日志文件列表，按文件名排序"""
    if paths:
        return sorted(Path(p) for p in paths if Path(p).exists())
    return sorted(LOG_DIR.glob("*.log"))


def parse_streaming_chunks(raw_body: str) -> dict:
    """
    解析流式 SSE 响应，合并所有 chunk。
    返回合并后的 content 和 tool_calls。
    """
    content_parts = []
    tool_calls: dict[int, dict] = {}  # index -> accumulated tool_call
    usage = None
    model = None
    finish_reason = None

    for line in raw_body.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        if line == "data: [DONE]":
            break
        if not line.startswith("data: "):
            continue

        json_str = line[6:]  # strip "data: "
        try:
            chunk = json.loads(json_str)
        except json.JSONDecodeError:
            continue

        if not chunk:
            continue

        # 提取 model
        if chunk.get("model"):
            model = chunk.get("model")

        # 提取 usage（在 lastOne=true 的 chunk 中）
        if chunk.get("lastOne"):
            usage = chunk.get("usage")

        choices = chunk.get("choices", [])
        for choice in choices:
            delta = choice.get("delta", {})

            # 合并 content
            c = delta.get("content")
            if c:
                content_parts.append(c)

            # 合并 tool_calls
            for tc in delta.get("tool_calls", []):
                idx = tc.get("index", 0)
                if idx not in tool_calls:
                    tool_calls[idx] = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
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

    # 按 index 排序 tool_calls
    merged_tool_calls = [tool_calls[i] for i in sorted(tool_calls.keys())] if tool_calls else None

    result = {
        "content": "".join(content_parts),
        "tool_calls": merged_tool_calls,
        "finish_reason": finish_reason,
        "model": model,
    }
    if usage:
        result["usage"] = usage
    return result


def extract_user_messages(body_str: str) -> list[dict]:
    """从 INPUT body 中提取用户消息（跳过 system 消息）"""
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
            # 处理多模态内容，只提取文本部分
            text_parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(item.get("text", ""))
            content = "\n".join(text_parts)
        result.append({"role": role, "content": content})
    return result


def parse_log_file(filepath: Path, no_system: bool = False, only_user: bool = False) -> list[dict]:
    """
    解析单个日志文件，返回对话记录列表。
    每个记录: {timestamp, model, user_input, assistant_output, tool_calls, finish_reason, usage}
    """
    records = []

    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()

    # 匹配所有记录起始位置
    pattern = re.compile(
        r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})  INFO  (>>>|<<<) (\w+)"
    )
    matches = list(pattern.finditer(text))

    for i, m in enumerate(matches):
        timestamp = m.group(1)
        direction = m.group(2)  # >>> or <<<
        record_type = m.group(3)  # INPUT / OUTPUT / ERROR

        # 提取从当前记录到下一个记录之间的内容
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[start:end].strip()

        if direction == ">>>":  # INPUT
            # 解析 BODY= 后的 JSON
            body_match = re.match(r"\s*MODEL=(\S+)\s+BODY=(.*)", block, re.DOTALL)
            if not body_match:
                continue
            model = body_match.group(1)
            body_str = body_match.group(2).strip()

            messages = extract_user_messages(body_str)

            # 提取用户输入
            user_contents = []
            for msg in messages:
                if msg["role"] == "user":
                    user_contents.append(msg["content"])
                elif msg["role"] == "system" and not no_system:
                    # system 消息通常很长，截断显示
                    content = msg["content"]
                    if len(content) > 200 and only_user:
                        continue
                    user_contents.append(f"[SYSTEM] {content[:500]}..." if len(content) > 500 else f"[SYSTEM] {content}")

            if only_user:
                user_contents = [c for c in user_contents if not c.startswith("[SYSTEM]")]

            if not user_contents and only_user:
                continue

            records.append({
                "timestamp": timestamp,
                "model": model,
                "direction": "input",
                "user_input": "\n".join(user_contents) if user_contents else "(no user message)",
                "assistant_output": None,
                "tool_calls": None,
                "finish_reason": None,
                "usage": None,
                "status": None,
            })

        elif direction == "<<<":  # OUTPUT
            body_match = re.match(r"\s*MODEL=(\S+)\s+STATUS=(\d+)\s+BODY=(.*)", block, re.DOTALL)
            if not body_match:
                # 尝试匹配 ERROR
                error_match = re.match(r"\s*MODEL=(\S+)\s+REASON=(.*)", block, re.DOTALL)
                if error_match:
                    records.append({
                        "timestamp": timestamp,
                        "model": error_match.group(1),
                        "direction": "error",
                        "user_input": None,
                        "assistant_output": None,
                        "tool_calls": None,
                        "finish_reason": "error",
                        "usage": None,
                        "status": "error",
                        "error_reason": error_match.group(2).strip(),
                    })
                continue

            model = body_match.group(1)
            status = int(body_match.group(2))
            body_str = body_match.group(3).strip()

            # 判断是否为流式响应
            if body_str.startswith("data:"):
                parsed = parse_streaming_chunks(body_str)
                assistant_output = parsed["content"]
                tool_calls = parsed.get("tool_calls")
                finish_reason = parsed.get("finish_reason")
                usage = parsed.get("usage")
            else:
                # 非流式响应
                try:
                    data = json.loads(body_str)
                    choices = data.get("choices", [])
                    assistant_output = ""
                    tool_calls = None
                    finish_reason = None
                    for choice in choices:
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
                "timestamp": timestamp,
                "model": model,
                "direction": "output",
                "user_input": None,
                "assistant_output": assistant_output,
                "tool_calls": tool_calls,
                "finish_reason": finish_reason,
                "usage": usage,
                "status": status,
            })

    return records


def pair_conversations(records: list[dict]) -> list[dict]:
    """
    将 INPUT 和 OUTPUT 配对为完整对话轮次。
    每个对话: {timestamp, model, user_input, assistant_output, tool_calls, usage}
    """
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
                "assistant_output": None,
                "tool_calls": None,
                "finish_reason": "error",
                "usage": None,
                "status": "error",
                "error_reason": rec.get("error_reason", ""),
            })
            current_input = None

    return conversations


def format_text_report(conversations: list[dict], filepath: str = "", max_input_len: int = 2000, max_output_len: int = 5000) -> str:
    """生成纯文本格式报告"""
    lines = []
    lines.append("=" * 80)
    lines.append(f"LLM Proxy 日志分析报告")
    if filepath:
        lines.append(f"日志文件: {filepath}")
    lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"对话轮次: {len(conversations)}")
    lines.append("=" * 80)

    for i, conv in enumerate(conversations, 1):
        lines.append("")
        lines.append(f"--- 对话 #{i} | {conv['timestamp']} | 模型: {conv['model']} ---")
        lines.append("")

        # 用户输入
        user_input = conv["user_input"] or "(空)"
        if len(user_input) > max_input_len:
            user_input = user_input[:max_input_len] + f"\n... (截断，共 {len(conv['user_input'] or '')} 字符)"
        lines.append(f"[用户输入]")
        lines.append(user_input)
        lines.append("")

        # LLM 输出
        if conv.get("status") == "error":
            lines.append(f"[错误] {conv.get('error_reason', 'Unknown error')}")
        else:
            assistant_output = conv["assistant_output"] or "(空)"
            if len(assistant_output) > max_output_len:
                assistant_output = assistant_output[:max_output_len] + f"\n... (截断，共 {len(conv['assistant_output'] or '')} 字符)"
            lines.append(f"[LLM 输出] finish_reason={conv.get('finish_reason', 'N/A')}")
            lines.append(assistant_output)

            # Tool calls
            if conv.get("tool_calls"):
                lines.append("")
                lines.append(f"[工具调用] {len(conv['tool_calls'])} 个")
                for j, tc in enumerate(conv["tool_calls"]):
                    fn = tc.get("function", {})
                    args = fn.get("arguments", "")
                    # 尝试格式化 JSON 参数
                    try:
                        args_obj = json.loads(args)
                        args_str = json.dumps(args_obj, ensure_ascii=False, indent=2)
                    except (json.JSONDecodeError, TypeError):
                        args_str = args
                    lines.append(f"  [{j+1}] {fn.get('name', '?')}({args_str[:300]})")

            # Usage
            if conv.get("usage"):
                u = conv["usage"]
                lines.append("")
                lines.append(f"[Token 用量] prompt={u.get('prompt_tokens', '?')} completion={u.get('completion_tokens', '?')} total={u.get('total_tokens', '?')}")

        lines.append("")
        lines.append("-" * 80)

    return "\n".join(lines)


def format_json_report(conversations: list[dict]) -> str:
    """生成 JSON 格式报告"""
    return json.dumps(conversations, ensure_ascii=False, indent=2)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="LLM Proxy 日志分析工具")
    parser.add_argument("files", nargs="*", help="日志文件路径（默认分析 logs/ 下所有 .log）")
    parser.add_argument("--all", action="store_true", help="分析所有日志文件")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="输出格式")
    parser.add_argument("--output", "-o", help="输出文件路径（默认输出到 stdout）")
    parser.add_argument("--no-system", action="store_true", help="跳过 system 消息")
    parser.add_argument("--only-user", action="store_true", help="只保留 user 角色输入")
    parser.add_argument("--max-input-len", type=int, default=2000, help="用户输入最大显示长度")
    parser.add_argument("--max-output-len", type=int, default=5000, help="LLM 输出最大显示长度")
    parser.add_argument("--stats", action="store_true", help="仅输出统计信息")

    args = parser.parse_args()

    # 获取日志文件
    if args.files:
        log_files = find_log_files(args.files)
    else:
        log_files = find_log_files()

    if not log_files:
        print("未找到日志文件", file=sys.stderr)
        sys.exit(1)

    all_conversations = []

    for log_file in log_files:
        print(f"正在分析: {log_file.name} ...", file=sys.stderr)
        records = parse_log_file(log_file, no_system=args.no_system, only_user=args.only_user)
        conversations = pair_conversations(records)
        all_conversations.extend(conversations)
        print(f"  -> {len(conversations)} 轮对话", file=sys.stderr)

    if args.stats:
        # 仅输出统计
        total = len(all_conversations)
        errors = sum(1 for c in all_conversations if c.get("status") == "error")
        total_input_tokens = sum(
            (c.get("usage") or {}).get("prompt_tokens", 0) or 0 for c in all_conversations
        )
        total_output_tokens = sum(
            (c.get("usage") or {}).get("completion_tokens", 0) or 0 for c in all_conversations
        )
        models = {}
        for c in all_conversations:
            m = c.get("model", "unknown")
            models[m] = models.get(m, 0) + 1

        stats = {
            "total_conversations": total,
            "errors": errors,
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "models": models,
        }
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return

    # 生成报告
    if args.format == "json":
        report = format_json_report(all_conversations)
    else:
        report = format_text_report(
            all_conversations,
            filepath=", ".join(str(f.name) for f in log_files),
            max_input_len=args.max_input_len,
            max_output_len=args.max_output_len,
        )

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\n报告已保存到: {args.output}", file=sys.stderr)
    else:
        print(report)


if __name__ == "__main__":
    main()
