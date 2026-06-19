"""
Flask Web 应用 - 中国煤炭月度产量数据监测
=========================================
提供数据查询 API 和前端页面。

API 端点:
  GET /api/production?region=全国&months=12
  GET /api/production/regions
  GET /api/production/statistics
  GET /api/production/table?region=全国&months=6

前端页面:
  GET /   → index.html
"""

import logging
import sys
import os
from datetime import datetime, timedelta

from flask import Flask, jsonify, request, render_template, send_from_directory, Response

# 确保项目根目录在 Python 路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import SERVER, PROVINCE_CODES, LOG_CONFIG, DATA_DIR
from models import (
    init_db, get_production, get_all_regions,
    get_statistics, get_recent_table, get_all_available_regions,
    get_data_range, upsert_production, get_detailed_table,
    get_download_csv,
)
from fetcher import safe_fetch, generate_seed_data, fetch_coal_production

# ─── 日志配置 ──────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, LOG_CONFIG["level"]),
    format=LOG_CONFIG["format"],
    datefmt=LOG_CONFIG["date_format"],
)
logger = logging.getLogger(__name__)

# 抑制 requests/urllib3 的 SSL 警告
import warnings
warnings.filterwarnings("ignore", message="Unverified HTTPS request")

# ─── Flask 应用 ────────────────────────────────────────────

app = Flask(__name__)


# ═══════════════════════════════════════════════════════════════
# 初始化
# ═══════════════════════════════════════════════════════════════

def initialize_data():
    """启动时初始化数据库并尝试抓取数据。"""
    init_db()
    logger.info("数据库初始化完成")

    # 检查是否已有数据
    stats = get_statistics()
    if stats["record_count"] > 0:
        logger.info("数据库已有 %d 条记录 (%d 个地区, %s ~ %s)",
                    stats["record_count"], stats["region_count"],
                    stats["min_ym"], stats["max_ym"])
        return

    # 尝试从 API 获取初始数据
    logger.info("数据库为空，尝试从国家统计局 API 获取初始数据...")
    try:
        df = safe_fetch()
        if df is not None and not df.empty:
            records = []
            for _, row in df.iterrows():
                records.append((row["region"], row["year_month"], row["output"]))
            from models import bulk_upsert
            count = bulk_upsert(records)
            logger.info("成功导入 %d 条初始数据", count)
        else:
            logger.warning("无法获取初始数据")
    except Exception as e:
        logger.error("初始化数据失败: %s", e)


# ═══════════════════════════════════════════════════════════════
# API 端点
# ═══════════════════════════════════════════════════════════════

@app.route("/api/production")
def api_production():
    """
    获取指定地区的产量数据。

    Query parameters:
        region: 地区名称，默认"全国"
        months: 最近几个月，默认 12

    Returns:
        JSON: {region, data: [{year_month, output}, ...]}
    """
    region = request.args.get("region", "全国")
    try:
        months = int(request.args.get("months", 12))
        months = max(1, min(120, months))
    except ValueError:
        months = 12

    data = get_production(region, months)
    return jsonify({
        "region": region,
        "months": months,
        "data": data,
    })


@app.route("/api/production/regions")
def api_regions():
    """获取所有可查询的地区列表。"""
    regions = get_all_available_regions()
    return jsonify({
        "regions": regions,
        "total": len(regions),
    })


@app.route("/api/production/statistics")
def api_statistics():
    """获取数据统计信息。"""
    stats = get_statistics()
    return jsonify(stats)


@app.route("/api/production/table")
def api_table():
    """获取详细数据表格（含环比、同比、累计值）。"""
    region = request.args.get("region", "全国")
    try:
        months = int(request.args.get("months", 6))
        months = max(1, min(60, months))
    except ValueError:
        months = 6

    data = get_detailed_table(region, months)
    return jsonify({
        "region": region,
        "months": months,
        "data": data,
    })


@app.route("/api/production/refresh")
def api_refresh():
    """
    手动触发数据刷新。
    从 API 获取最新数据并存入数据库。

    Returns:
        JSON: {status, message, count}
    """
    logger.info("手动触发数据刷新...")
    try:
        df = safe_fetch()
        if df is None or df.empty:
            return jsonify({
                "status": "error",
                "message": "未能获取到数据",
            }), 500

        from models import bulk_upsert
        records = [(row["region"], row["year_month"], row["output"])
                   for _, row in df.iterrows()]
        count = bulk_upsert(records)

        return jsonify({
            "status": "success",
            "message": f"成功更新 {count} 条新记录",
            "count": count,
            "total": len(df),
        })
    except Exception as e:
        logger.error("刷新数据失败: %s", e)
        return jsonify({
            "status": "error",
            "message": f"刷新失败: {str(e)}",
        }), 500


@app.route("/api/production/seed")
def api_seed():
    """
    生成并导入种子数据。
    用于 API 不可用时的演示。

    Returns:
        JSON: {status, message, count}
    """
    try:
        df = generate_seed_data()
        from models import bulk_upsert
        records = [(row["region"], row["year_month"], row["output"])
                   for _, row in df.iterrows()]
        count = bulk_upsert(records)
        logger.info("导入 %d 条种子数据", count)
        return jsonify({
            "status": "success",
            "message": f"成功导入 {count} 条种子数据",
            "count": count,
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e),
        }), 500


@app.route("/api/production/range")
def api_range():
    """获取数据库中数据的时间范围。"""
    min_ym, max_ym = get_data_range()
    return jsonify({
        "min": min_ym,
        "max": max_ym,
    })


@app.route("/api/production/download")
def api_download():
    """一键下载 CSV 数据。

    Query parameters:
        region: 地区名称，默认"全国"
        months: 最近几个月，默认 12

    Returns:
        CSV 文件下载
    """
    region = request.args.get("region", "全国")
    try:
        months = int(request.args.get("months", 12))
        months = max(1, min(120, months))
    except ValueError:
        months = 12

    csv_content = get_download_csv(region, months)
    filename = f"coal_production_{region}_{datetime.now().strftime('%Y%m')}.csv"

    # 使用 RFC 5987 标准支持中文文件名
    from urllib.parse import quote
    encoded_filename = quote(filename, safe="")
    return Response(
        csv_content,
        mimetype="text/csv; charset=utf-8-sig",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
        },
    )


# ═══════════════════════════════════════════════════════════════
# 前端页面
# ═══════════════════════════════════════════════════════════════

@app.route("/")
def index():
    """提供前端单页面。"""
    return render_template("index.html",
                           provinces=PROVINCE_CODES,
                           current_year=datetime.now().year)


@app.route("/static/<path:filename>")
def serve_static(filename):
    """提供静态文件。"""
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    return send_from_directory(static_dir, filename)


# ═══════════════════════════════════════════════════════════════
# 启动
# ═══════════════════════════════════════════════════════════════

def main():
    """启动 Web 服务器。"""
    with app.app_context():
        initialize_data()

    logger.info("=" * 60)
    logger.info("中国煤炭月度产量数据监测系统")
    logger.info(f"访问地址: http://localhost:{SERVER['port']}")
    logger.info(f"API 文档:")
    logger.info(f"  GET /api/production?region=全国&months=12")
    logger.info(f"  GET /api/production/regions")
    logger.info(f"  GET /api/production/table?region=全国&months=6")
    logger.info(f"  GET /api/production/statistics")
    logger.info(f"  GET /api/production/refresh")
    logger.info("=" * 60)

    app.run(
        host=SERVER["host"],
        port=SERVER["port"],
        debug=SERVER["debug"],
    )


if __name__ == "__main__":
    main()
