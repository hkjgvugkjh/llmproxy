"""Core proxy server module."""

import json
import logging
import os
from datetime import datetime
from threading import Lock
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

REPORT_DIR = "reports"
os.makedirs(REPORT_DIR, exist_ok=True)

_log = logging.getLogger("llmproxy")
_log.setLevel(logging.INFO)
if not _log.handlers:
    _log.addHandler(logging.StreamHandler())

_report_write_lock = Lock()


def _extract_user_input(req_json: dict) -> str:
    messages = req_json.get("messages", [])
    parts = []
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, list):
                text_parts = [
                    item.get("text", "")
                    for item in content
                    if isinstance(item, dict) and item.get("type") == "text"
                ]
                content = "\n".join(text_parts)
            parts.append(content)
    return "\n".join(parts) if parts else "(no user message)"


def _parse_output(output_text: str) -> dict:
    result: dict = {"content": "", "tool_calls": None, "finish_reason": None, "usage": None}
    if output_text.lstrip().startswith("data:"):
        content_parts: list[str] = []
        tc_map: dict[int, dict] = {}
        for line in output_text.strip().splitlines():
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
            if chunk.get("lastOne"):
                result["usage"] = chunk.get("usage")
            for choice in chunk.get("choices", []):
                delta = choice.get("delta", {})
                c = delta.get("content")
                if c:
                    content_parts.append(c)
                for tc in delta.get("tool_calls", []):
                    idx = tc.get("index", 0)
                    if idx not in tc_map:
                        tc_map[idx] = {"id": "", "type": "function",
                                       "function": {"name": "", "arguments": ""}}
                    if tc.get("id"):
                        tc_map[idx]["id"] = tc["id"]
                    if tc.get("type"):
                        tc_map[idx]["type"] = tc["type"]
                    fn = tc.get("function", {})
                    if fn.get("name"):
                        tc_map[idx]["function"]["name"] = fn["name"]
                    if fn.get("arguments"):
                        tc_map[idx]["function"]["arguments"] += fn["arguments"]
                fr = choice.get("finish_reason")
                if fr:
                    result["finish_reason"] = fr
        result["content"] = "".join(content_parts)
        if tc_map:
            result["tool_calls"] = [tc_map[i] for i in sorted(tc_map)]
    else:
        try:
            data = json.loads(output_text)
        except json.JSONDecodeError:
            result["content"] = output_text[:500]
            return result
        for choice in data.get("choices", []):
            msg = choice.get("message", {})
            if msg.get("content"):
                result["content"] += msg["content"]
            if msg.get("tool_calls"):
                result["tool_calls"] = msg["tool_calls"]
            if choice.get("finish_reason"):
                result["finish_reason"] = choice["finish_reason"]
        result["usage"] = data.get("usage")
    return result


def _format_tool_calls(tool_calls: list[dict]) -> str:
    lines = []
    for i, tc in enumerate(tool_calls):
        fn = tc.get("function", {})
        args = fn.get("arguments", "")
        try:
            args_str = json.dumps(json.loads(args), ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            args_str = args
        lines.append(f"  [{i+1}] {fn.get('name', '?')}({args_str[:300]})")
    return "\n".join(lines)


def append_report(now: datetime, model_name: str, req_json: dict,
                  output_text: str, status_code: int) -> None:
    ymd = now.strftime("%Y%m%d")
    report_path = os.path.join(REPORT_DIR, f"{ymd}-analysis.txt")
    user_input = _extract_user_input(req_json)
    parsed = _parse_output(output_text)
    _report_write_lock.acquire()
    try:
        with open(report_path, "a", encoding="utf-8") as f:
            f.write(f"\n{'─'*70}\n")
            f.write(f"时间: {now.strftime('%Y-%m-%d %H:%M:%S')}  ")
            f.write(f"模型: {model_name}  ")
            f.write(f"状态: {status_code}  ")
            f.write(f"finish: {parsed.get('finish_reason') or 'N/A'}\n\n")
            f.write(f"[用户输入]\n{user_input}\n\n")
            if parsed.get("tool_calls"):
                f.write(f"[工具调用] {len(parsed['tool_calls'])} 个\n")
                f.write(_format_tool_calls(parsed["tool_calls"]))
                f.write("\n\n")
            f.write(f"[LLM 输出]\n{parsed['content']}\n")
            if parsed.get("usage"):
                u = parsed["usage"]
                f.write(f"\n[Token] prompt={u.get('prompt_tokens','?')} "
                        f"completion={u.get('completion_tokens','?')} "
                        f"total={u.get('total_tokens','?')}\n")
    finally:
        _report_write_lock.release()


def create_app(config_path: Optional[str] = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    import configparser

    if config_path is None:
        config_path = os.environ.get("LLMPROXY_CONFIG", "config.ini")

    cfg = configparser.ConfigParser()
    cfg.read(config_path)

    models: dict[str, tuple[str, str, str]] = {}
    for name, val in cfg["models"].items():
        if "|" in val:
            key, base, model = val.split("|", 2)
            models[name] = (key.strip(), base.strip(), model.strip())

    proxy_host = cfg.get("proxy", "host", fallback="0.0.0.0")
    proxy_port = cfg.getint("proxy", "port", fallback=8000)
    proxy_api_key = cfg.get("auth", "proxy_api_key", fallback="")

    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)

    def get_logger(ymd: str) -> logging.Logger:
        fh = logging.FileHandler(os.path.join(log_dir, f"{ymd}.log"), encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)s  %(message)s"))
        _log.handlers = [h for h in _log.handlers if not isinstance(h, logging.FileHandler)]
        _log.addHandler(fh)
        return _log

    def verify_auth(auth_header: Optional[str]):
        if not proxy_api_key:
            return
        if not auth_header:
            raise HTTPException(401, "Missing Authorization header")
        if auth_header.removeprefix("Bearer ").strip() != proxy_api_key:
            raise HTTPException(403, "Invalid proxy API key")

    app = FastAPI(title="LLM Proxy", version="1.0.0")

    @app.get("/v1/version")
    async def get_version(request: Request):
        verify_auth(request.headers.get("Authorization"))
        return JSONResponse({"version": "1.0.0", "name": "llmproxy"})

    @app.get("/v1/props")
    async def get_props(request: Request):
        verify_auth(request.headers.get("Authorization"))
        return JSONResponse({
            "models": list(models.keys()),
            "host": proxy_host,
            "port": proxy_port,
            "auth_enabled": bool(proxy_api_key),
        })

    @app.get("/v1/models")
    async def list_models(request: Request):
        verify_auth(request.headers.get("Authorization"))
        data = [{"id": m, "object": "model",
                 "created": int(datetime.now().timestamp()),
                 "owned_by": "llmproxy"} for m in models]
        return JSONResponse({"object": "list", "data": data})

    @app.api_route("/v1/{path:path}", methods=["POST", "GET", "PUT", "DELETE", "PATCH"])
    async def proxy(request: Request, path: str):
        verify_auth(request.headers.get("Authorization"))
        body_bytes = await request.body()
        try:
            req_json = json.loads(body_bytes) if body_bytes else {}
        except json.JSONDecodeError:
            raise HTTPException(400, "Invalid JSON body")

        model_name = req_json.get("model", "").lower()
        if not model_name or model_name not in models:
            raise HTTPException(400, f"Unsupported model '{model_name}'. Available: {list(models.keys())}")

        api_key, base_url, model = models[model_name]
        req_json["model"] = model
        target_url = f"{base_url.rstrip('/')}/{path}"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        input_text = json.dumps(req_json)
        now = datetime.now()
        ymd = now.strftime("%Y%m%d")
        log = get_logger(ymd)
        log.info(">>> INPUT  MODEL=%(model)s  BODY=%(body)s",
                 {"name": model_name, "model": model, "body": input_text})

        async with httpx.AsyncClient(timeout=300) as client:
            try:
                resp = await client.request(method=request.method, url=target_url,
                                            headers=headers, content=input_text)
                content_type = resp.headers.get("content-type", "")

                if "text/event-stream" in content_type:
                    output_chunks: list[bytes] = []

                    async def stream_with_capture():
                        async for chunk in resp.aiter_bytes():
                            output_chunks.append(chunk)
                            yield chunk
                        output_text = b"".join(output_chunks).decode("utf-8", errors="replace")
                        append_report(now, model_name, req_json, output_text, resp.status_code)

                    log.info("<<< OUTPUT MODEL=%(model)s  STATUS=%(status)d  STREAMING=true",
                             {"model": model_name, "status": resp.status_code})
                    return StreamingResponse(
                        stream_with_capture(), status_code=resp.status_code,
                        media_type="text/event-stream",
                        headers={k: v for k, v in resp.headers.items() if k.lower() != "content-encoding"},
                    )
                else:
                    output_text = resp.text
                    log.info("<<< OUTPUT MODEL=%(model)s  STATUS=%(status)d  BODY=%(body)s",
                             {"model": model_name, "status": resp.status_code, "body": output_text})
                    append_report(now, model_name, req_json, output_text, resp.status_code)
                    return Response(content=output_text, status_code=resp.status_code,
                                    media_type=content_type)

            except httpx.RequestError as e:
                log.error("<<< ERROR  MODEL=%(model)s  REASON=%(reason)s",
                          {"model": model_name, "reason": str(e)})
                raise HTTPException(502, f"Upstream request failed: {e}")

    return app
