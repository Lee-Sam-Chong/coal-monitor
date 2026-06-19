"""
数据模型层 - SQLite 数据库操作
================================
提供所有数据库操作接口，包括建表、插入、查询等。
"""

import sqlite3
import logging
from datetime import datetime
from contextlib import contextmanager
from config import DB_PATH

logger = logging.getLogger(__name__)

# ─── 数据库连接管理 ────────────────────────────────────────


@contextmanager
def get_connection():
    """获取数据库连接的上下文管理器，自动提交/回滚并关闭。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─── 初始化 ────────────────────────────────────────────────

def init_db():
    """创建数据库表（如果不存在）。"""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS monthly_production (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                region      TEXT    NOT NULL,
                year_month  TEXT    NOT NULL,  -- 格式: YYYY-MM
                output      REAL    NOT NULL,  -- 产量（万吨）
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(region, year_month)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_region_ym
            ON monthly_production(region, year_month)
        """)
    logger.info("数据库初始化完成: %s", DB_PATH)


# ─── 插入操作 ──────────────────────────────────────────────

def upsert_production(region, year_month, output):
    """
    插入或更新一条产量记录。

    Args:
        region: 地区名称（如"全国"、"山西省"）
        year_month: 年月，格式 "YYYY-MM"
        output: 产量值（万吨）

    Returns:
        bool: 是否新增了记录（而非更新）
    """
    with get_connection() as conn:
        cursor = conn.execute("""
            INSERT INTO monthly_production (region, year_month, output)
            VALUES (?, ?, ?)
            ON CONFLICT(region, year_month)
            DO UPDATE SET output = excluded.output,
                          updated_at = CURRENT_TIMESTAMP
        """, (region, year_month, output))
        # 判断是否新增：如果影响的行数中第一行是新增的
        inserted = cursor.rowcount > 0 and cursor.lastrowid is not None
        return inserted


def bulk_upsert(records):
    """
    批量插入/更新产量记录。

    Args:
        records: list of (region, year_month, output) tuples

    Returns:
        int: 新增记录数
    """
    count = 0
    with get_connection() as conn:
        for region, year_month, output in records:
            cursor = conn.execute("""
                INSERT INTO monthly_production (region, year_month, output)
                VALUES (?, ?, ?)
                ON CONFLICT(region, year_month)
                DO UPDATE SET output = excluded.output,
                              updated_at = CURRENT_TIMESTAMP
            """, (region, year_month, output))
            if cursor.lastrowid is not None:
                count += 1
    return count


# ─── 查询操作 ──────────────────────────────────────────────

def exists(region, year_month):
    """检查指定地区和年月的数据是否已存在。"""
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT 1 FROM monthly_production WHERE region = ? AND year_month = ?",
            (region, year_month)
        )
        return cursor.fetchone() is not None


def get_latest_year_month():
    """获取数据库中最近的年月。"""
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT year_month FROM monthly_production ORDER BY year_month DESC LIMIT 1"
        )
        row = cursor.fetchone()
        return row["year_month"] if row else None


def get_production(region, months=12):
    """
    获取指定地区最近 N 个月的产量数据。

    Args:
        region: 地区名称
        months: 最近几个月

    Returns:
        list of dict: [{"year_month": "2025-06", "output": 3.8}, ...]
    """
    with get_connection() as conn:
        cursor = conn.execute("""
            SELECT year_month, output
            FROM monthly_production
            WHERE region = ?
            ORDER BY year_month DESC
            LIMIT ?
        """, (region, months))
        rows = cursor.fetchall()

    # 按时间正序返回
    result = [
        {"year_month": row["year_month"], "output": row["output"]}
        for row in reversed(rows)
    ]
    return result


def get_detailed_table(region, months=6):
    """
    获取详细产量数据（含环比、同比、累计值计算）。

    Returns:
        list of dict:
        - year_month:    年月
        - output:        当月值（万吨）
        - mom_change:    环比变化绝对值
        - mom_change_pct: 环比变化百分比
        - yoy_change_pct: 同比变化百分比
        - ytd:           年初至当月累计值
        - ytd_yoy_pct:   累计同比变化百分比
    """
    with get_connection() as conn:
        cursor = conn.execute("""
            SELECT year_month, output
            FROM monthly_production
            WHERE region = ?
            ORDER BY year_month ASC
        """, (region,))
        all_rows = cursor.fetchall()

    if not all_rows:
        return []

    data_by_ym = {row["year_month"]: row["output"] for row in all_rows}
    ym_list = sorted(data_by_ym.keys())

    # 只返回最近 N 个月
    target_yms = ym_list[-months:]

    result = []
    for ym in target_yms:
        output = data_by_ym[ym]
        year, month = ym.split("-")
        month_num = int(month)
        year_int = int(year)

        # ── 环比：跟上个月比 ──
        prev_ym = f"{year}-{str(month_num - 1).zfill(2)}" if month_num > 1 else None
        prev_output = data_by_ym.get(prev_ym) if prev_ym else None
        mom_change = (output - prev_output) if prev_output is not None else None
        mom_pct = ((output - prev_output) / prev_output * 100) if prev_output and prev_output != 0 else None

        # ── 同比：跟去年同月比 ──
        yoy_ym = f"{year_int - 1}-{month}"
        yoy_output = data_by_ym.get(yoy_ym)
        yoy_pct = ((output - yoy_output) / yoy_output * 100) if yoy_output and yoy_output != 0 else None

        # ── 累计值：当年 1 月到当月 ──
        ytd = sum(
            data_by_ym.get(f"{year}-{str(m).zfill(2)}", 0)
            for m in range(1, month_num + 1)
        )

        # ── 累计同比 ──
        ytd_last = sum(
            data_by_ym.get(f"{year_int - 1}-{str(m).zfill(2)}", 0)
            for m in range(1, month_num + 1)
        )
        ytd_yoy_pct = ((ytd - ytd_last) / ytd_last * 100) if ytd_last != 0 else None

        result.append({
            "year_month": ym,
            "output": round(output, 2),
            "mom_change": round(mom_change, 2) if mom_change is not None else None,
            "mom_change_pct": round(mom_pct, 2) if mom_pct is not None else None,
            "yoy_change_pct": round(yoy_pct, 2) if yoy_pct is not None else None,
            "ytd": round(ytd, 2),
            "ytd_yoy_pct": round(ytd_yoy_pct, 2) if ytd_yoy_pct is not None else None,
        })

    return result


def get_download_csv(region="全国", months=12):
    """
    生成 CSV 下载内容。

    Returns:
        str: CSV 格式的完整文本
    """
    import csv
    import io

    data = get_detailed_table(region, months)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "年月", "当月值(万吨)", "环比变化", "环比变化(%)",
        "同比变化(%)", "累计值(万吨)", "累计同比(%)"
    ])
    for d in data:
        writer.writerow([
            d["year_month"],
            d["output"],
            d.get("mom_change", ""),
            d.get("mom_change_pct", ""),
            d.get("yoy_change_pct", ""),
            d.get("ytd", ""),
            d.get("ytd_yoy_pct", ""),
        ])
    return buf.getvalue()


def get_all_regions():
    """获取数据库中有数据的所有地区列表。"""
    with get_connection() as conn:
        cursor = conn.execute("""
            SELECT DISTINCT region
            FROM monthly_production
            ORDER BY region
        """)
        rows = cursor.fetchall()
    return [row["region"] for row in rows]


def get_all_available_regions():
    """获取所有可查询的地区列表（包括有数据和配置中定义的所有省份）。"""
    from config import PROVINCE_CODES, PROVINCE_NAMES
    # 从配置中获取所有省份名 + 数据库中有数据的地区
    configured = list(PROVINCE_CODES.values())

    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT DISTINCT region FROM monthly_production"
        )
        db_regions = [row["region"] for row in cursor.fetchall()]

    # 合并并去重，保持逻辑顺序：全国在最前
    all_regions = list(dict.fromkeys(configured + db_regions))
    # 确保"全国"在第一位
    if "全国" in all_regions:
        all_regions.remove("全国")
        all_regions.insert(0, "全国")

    return all_regions


def get_data_range():
    """获取数据库中数据的时间范围。"""
    with get_connection() as conn:
        cursor = conn.execute("""
            SELECT MIN(year_month) as min_ym, MAX(year_month) as max_ym
            FROM monthly_production
        """)
        row = cursor.fetchone()
        if row and row["min_ym"]:
            return row["min_ym"], row["max_ym"]
        return None, None


def get_statistics():
    """获取数据统计信息。"""
    with get_connection() as conn:
        cursor = conn.execute("""
            SELECT
                COUNT(DISTINCT region) as region_count,
                COUNT(*) as record_count,
                MIN(year_month) as min_ym,
                MAX(year_month) as max_ym
            FROM monthly_production
        """)
        row = cursor.fetchone()
        if row:
            return dict(row)
        return {"region_count": 0, "record_count": 0, "min_ym": None, "max_ym": None}


def get_recent_table(region, months=6):
    """
    获取最近 N 个月的数据表格（含环比变化）。

    Args:
        region: 地区名称
        months: 最近几个月

    Returns:
        list of dict: [{"year_month": "...", "output": ..., "change": ...}, ...]
    """
    data = get_production(region, months)
    result = []
    prev = None
    for item in data:
        change = None
        if prev is not None:
            change = round(item["output"] - prev, 2) if item["output"] is not None and prev is not None else None
        result.append({
            "year_month": item["year_month"],
            "output": item["output"],
            "change": change,
        })
        prev = item["output"]
    return result


# ─── 清理 ──────────────────────────────────────────────────

def clear_all():
    """清空所有数据（用于测试/重置）。"""
    with get_connection() as conn:
        conn.execute("DELETE FROM monthly_production")
    logger.warning("已清空所有产量数据")


# ─── 直接执行 ──────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    print("数据库初始化完成")
    print(f"数据库路径: {DB_PATH}")
    stats = get_statistics()
    print(f"统计信息: {stats}")
