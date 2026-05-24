#!/bin/bash
# ============================================================
# LLM Proxy 每日日志分析脚本
# 分析前一天的日志，生成报告到 reports/ 目录
# 定时：每天早上 1:00 执行
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="${SCRIPT_DIR}/logs"
REPORT_DIR="${SCRIPT_DIR}/reports"
PYTHON="/home/tomac/miniconda3/bin/python3"
ANALYZER="${SCRIPT_DIR}/analyze_logs.py"

# 计算前一天日期
YESTERDAY=$(date -d "yesterday" +%Y%m%d)
YMD=$(date -d "yesterday" +%Y-%m-%d)

LOG_FILE="${LOG_DIR}/${YESTERDAY}.log"

# 确保报告目录存在
mkdir -p "${REPORT_DIR}"

REPORT_TEXT="${REPORT_DIR}/${YESTERDAY}-analysis.txt"
REPORT_JSON="${REPORT_DIR}/${YESTERDAY}-analysis.json"

echo "========================================"
echo "LLM Proxy 日志分析 - ${YMD}"
echo "========================================"
echo "日志文件: ${LOG_FILE}"
echo ""

# 检查日志文件是否存在
if [ ! -f "${LOG_FILE}" ]; then
    echo "警告: 日志文件不存在: ${LOG_FILE}"
    echo "尝试分析最近一天的可用日志..."
    
    # 找最近的日志文件
    LATEST_LOG=$(ls -1 "${LOG_DIR}"/*.log 2>/dev/null | sort | tail -1)
    if [ -z "${LATEST_LOG}" ]; then
        echo "错误: 未找到任何日志文件"
        exit 1
    fi
    LOG_FILE="${LATEST_LOG}"
    YESTERDAY=$(basename "${LATEST_LOG}" .log)
    echo "使用: ${LOG_FILE}"
fi

# 生成文本报告
echo "[1/3] 生成文本报告..."
"${PYTHON}" "${ANALYZER}" "${LOG_FILE}" \
    --only-user \
    --max-input-len 3000 \
    --max-output-len 8000 \
    --format text \
    --output "${REPORT_TEXT}"

# 生成 JSON 报告
echo "[2/3] 生成 JSON 报告..."
"${PYTHON}" "${ANALYZER}" "${LOG_FILE}" \
    --only-user \
    --format json \
    --output "${REPORT_JSON}"

# 生成统计摘要
echo "[3/3] 生成统计摘要..."
STATS=$("${PYTHON}" "${ANALYZER}" "${LOG_FILE}" --only-user --stats)

echo ""
echo "========================================"
echo "统计摘要 - ${YMD}"
echo "========================================"
echo "${STATS}" | "${PYTHON}" -m json.tool 2>/dev/null || echo "${STATS}"
echo ""
echo "报告文件:"
echo "  文本: ${REPORT_TEXT}"
echo "  JSON: ${REPORT_JSON}"
echo "========================================"
echo "分析完成: $(date '+%Y-%m-%d %H:%M:%S')"
