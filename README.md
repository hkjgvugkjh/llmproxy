# LLM Proxy

OpenAI 兼容的 LLM 反向代理，支持多模型路由、请求日志记录和实时对话分析报告。

## 功能特性

- **多模型路由**：通过 `config.ini` 配置多个上游 LLM 客户端，统一用 OpenAI 格式调用
- **请求日志**：每次请求/响应自动记录到 `logs/YYYYMMDD.log`
- **实时分析报告**：每个请求完成后自动追加到 `reports/YYYYMMDD-analysis.txt`，包含：
  - 用户输入（仅 user 角色）
  - LLM 完整输出（流式响应自动合并 SSE chunk）
  - 工具调用详情
  - Token 用量
- **流式响应支持**：边转发边收集，不改变流式行为
- **并发安全**：多请求并发写入报告时自动加锁
- **独立日志分析工具**：`analyze_logs.py` 支持对历史日志做离线分析和统计

## 环境要求

- Python 3.10+
- 依赖：`fastapi` `httpx` `uvicorn`

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置

编辑 `config.ini`，填入你的上游 LLM 信息：

```ini
[models]
my-model = YOUR_API_KEY|https://api.openai.com/v1|gpt-4o

[proxy]
host = 0.0.0.0
port = 8000

[auth]
proxy_api_key = my-secret-key   # 可选，留空不校验
```

### 3. 启动

```bash
# 前台启动
python3 llm_proxy.py

# 后台启动
./start.sh

# systemd 服务
sudo cp llmproxy.service /etc/systemd/system/
sudo systemctl enable --now llmproxy
```

### 4. 调用示例

```bash
# 列出可用模型
curl -s http://localhost:8000/v1/models \
  -H "Authorization: Bearer my-secret-key" | jq

# 对话
curl -s http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer my-secret-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"my-model","messages":[{"role":"user","content":"你好"}]}'
```

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/v1/version` | 版本信息 |
| GET | `/v1/props` | 配置信息（模型列表等） |
| GET | `/v1/models` | 模型列表（OpenAI 格式） |
| ALL | `/v1/{path}` | 透传到上游（支持 POST/GET/PUT/DELETE/PATCH） |

## 文件结构

```
llmproxy/
├── llm_proxy.py          # 主程序（代理 + 实时报告）
├── analyze_logs.py       # 离线日志分析工具
├── daily_analyze.sh      # 每日分析定时脚本
├── config.ini            # 配置文件（需自行填写）
├── requirements.txt      # Python 依赖
├── start.sh              # 后台启动脚本
├── llmproxy.service      # systemd 服务文件
├── README.md             # 本文件
├── logs/                 # 请求日志（自动生成）
│   └── YYYYMMDD.log
└── reports/              # 对话分析报告（自动生成）
    └── YYYYMMDD-analysis.txt
```

## 日志分析工具

`analyze_logs.py` 支持对历史日志做离线分析：

```bash
# 分析指定文件，输出文本报告
python3 analyze_logs.py logs/20260523.log --only-user

# 输出 JSON 格式
python3 analyze_logs.py logs/20260523.log --format json -o result.json

# 仅统计信息
python3 analyze_logs.py logs/20260523.log --stats

# 分析所有日志
python3 analyze_logs.py --all --stats
```

## 报告格式示例

```
──────────────────────────────────────────────────────────────────────
时间: 2026-05-23 15:00:00  模型: my-model  状态: 200  finish: stop

[用户输入]
你好，请介绍一下你自己

[LLM 输出]
你好！我是一个 AI 助手，可以帮你完成各种任务...

[Token] prompt=128 completion=64 total=192
```

## License

MIT
