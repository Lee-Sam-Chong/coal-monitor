"""
静态数据导出脚本（供 GitHub Actions 使用）
===========================================
从国家统计局 API（或种子数据）获取产量数据，
导出为 docs/data.json 和 docs/data.csv，
供 GitHub Pages 静态站点使用。

在 GitHub Actions 中运行：
  python export_static.py

生成文件：
  docs/data.json     — 全部数据的 JSON 格式
  docs/data.csv      — 全部数据的 CSV 格式（含明细列）
"""

import sys
import os
import json
import csv
import io
import logging
from datetime import datetime

# 确保项目根目录在 Python 路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import DATA_DIR, PROVINCE_CODES
from fetcher import safe_fetch, generate_seed_data
from models import init_db, bulk_upsert, get_statistics, get_download_csv, get_detailed_table

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("export")

import warnings
warnings.filterwarnings("ignore", message="Unverified HTTPS request")


# 导出目录（GitHub Pages 根目录）
DOCS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")


def export_all():
    """
    主导出函数：抓取数据 → 写入 docs/data.json → 写入 docs/data.csv
    """
    # 1. 初始化数据库并获取数据
    init_db()
    logger.info("数据库初始化完成")

    stats = get_statistics()
    if stats["record_count"] == 0:
        logger.info("数据库为空，尝试抓取数据...")
        df = safe_fetch()
        if df is not None and not df.empty:
            records = [(row["region"], row["year_month"], row["output"])
                       for _, row in df.iterrows()]
            count = bulk_upsert(records)
            logger.info("导入 %d 条数据", count)
        else:
            logger.error("无法获取数据，导出终止")
            sys.exit(1)
    else:
        logger.info("数据库已有 %d 条记录", stats["record_count"])

    # 重新获取最新统计
    stats = get_statistics()
    logger.info("数据统计: %d 个地区, %d 条记录, %s ~ %s",
                stats["region_count"], stats["record_count"],
                stats["min_ym"], stats["max_ym"])

    # 2. 创建 docs 目录
    os.makedirs(DOCS_DIR, exist_ok=True)

    # 3. 导出 JSON（全部数据 + 详细计算）
    export_json(stats)

    # 4. 导出 CSV（全部地区合并）
    export_csv(stats)

    logger.info("=" * 50)
    logger.info("导出完成!")
    logger.info("  docs/data.json  — 全部数据 (%d 条)", stats["record_count"])
    logger.info("  docs/data.csv   — CSV 下载文件")
    logger.info("=" * 50)


def export_json(stats):
    """导出全部数据为 data.json（含详细的同比/环比/累计计算）。"""
    from models import get_production, get_all_available_regions

    regions = get_all_available_regions()

    # 为每个地区生成详细数据
    all_data = {}
    for region in regions:
        detail = get_detailed_table(region, months=120)  # 取出所有数据
        if detail:
            all_data[region] = detail

    # 构建导出的数据包
    package = {
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "metadata": {
            "regions": regions,
            "region_count": stats.get("region_count", 0),
            "record_count": stats.get("record_count", 0),
            "date_range": {
                "min": stats.get("min_ym"),
                "max": stats.get("max_ym"),
            },
            "provinces": PROVINCE_CODES,
        },
        "data": all_data,  # {region: [{year_month, output, mom_change, ...}, ...]}
    }

    json_path = os.path.join(DOCS_DIR, "data.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(package, f, ensure_ascii=False, indent=2)

    file_size = os.path.getsize(json_path) / 1024
    logger.info("JSON 导出: %s (%.1f KB)", json_path, file_size)


def export_csv(stats):
    """导出全部数据为 CSV（所有地区合并）。"""
    from models import get_detailed_table, get_all_available_regions

    regions = get_all_available_regions()

    csv_path = os.path.join(DOCS_DIR, "data.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "地区", "年月", "当月值(万吨)", "环比变化",
            "环比变化(%)", "同比变化(%)", "累计值(万吨)", "累计同比(%)"
        ])

        total_rows = 0
        for region in regions:
            detail = get_detailed_table(region, months=120)
            for d in detail:
                writer.writerow([
                    region,
                    d["year_month"],
                    d["output"],
                    d.get("mom_change", ""),
                    d.get("mom_change_pct", ""),
                    d.get("yoy_change_pct", ""),
                    d.get("ytd", ""),
                    d.get("ytd_yoy_pct", ""),
                ])
                total_rows += 1

    file_size = os.path.getsize(csv_path) / 1024
    logger.info("CSV 导出: %s (%.1f KB, %d 行)", csv_path, file_size, total_rows)


if __name__ == "__main__":
    export_all()
