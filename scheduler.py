"""
定时任务调度器 - APScheduler
=============================
每月 16 日凌晨 2:00 自动抓取最新数据。

用法:
  python scheduler.py          # 启动调度器（守护进程）
  python scheduler.py --now    # 立即执行一次并退出
  python scheduler.py --test   # 立即执行一次（不走调度器）
"""

import sys
import os
import time
import logging

# 确保项目根目录在 Python 路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from config import SCHEDULE, LOG_CONFIG, NBS_API
from models import init_db, exists, bulk_upsert, get_statistics
from fetcher import safe_fetch, fetch_coal_production

logging.basicConfig(
    level=getattr(logging, LOG_CONFIG["level"]),
    format=LOG_CONFIG["format"],
    datefmt=LOG_CONFIG["date_format"],
)
logger = logging.getLogger("scheduler")

# 抑制 SSL 警告
import warnings
warnings.filterwarnings("ignore", message="Unverified HTTPS request")


# ═══════════════════════════════════════════════════════════════
# 抓取任务
# ═══════════════════════════════════════════════════════════════

def fetch_and_store():
    """
    执行一次数据抓取并存入数据库。
    自动跳过已存在的记录。

    Returns:
        int: 新增记录数
    """
    logger.info("=" * 50)
    logger.info("开始执行定时抓取任务...")
    logger.info("-" * 50)

    try:
        # 计算需要抓取的时间范围
        now = datetime.now()
        # 抓取最近 24 个月的数据（确保覆盖最新月份）
        end_ym = f"{now.year}-{now.month:02d}"

        # 通过 API 获取数据
        df = safe_fetch()
        if df is None or df.empty:
            logger.warning("本次抓取未获取到数据")
            return 0

        # 过滤已存在的记录
        new_records = []
        skipped = 0
        for _, row in df.iterrows():
            region = row["region"]
            year_month = row["year_month"]
            output = row["output"]

            if exists(region, year_month):
                skipped += 1
            else:
                new_records.append((region, year_month, output))

        # 批量插入新记录
        if new_records:
            count = bulk_upsert(new_records)
            logger.info("✅ 新增 %d 条记录", count)
        else:
            count = 0
            logger.info("没有新记录需要插入")

        if skipped > 0:
            logger.info("⏭️ 跳过 %d 条已存在的记录", skipped)

        # 打印统计
        stats = get_statistics()
        logger.info("当前数据库: %d 条记录, %d 个地区",
                    stats["record_count"], stats["region_count"])

        logger.info("-" * 50)
        logger.info("定时抓取任务完成")
        logger.info("=" * 50)

        return count

    except Exception as e:
        logger.error("❌ 抓取任务失败: %s", e, exc_info=True)
        return 0


# ═══════════════════════════════════════════════════════════════
# 调度器
# ═══════════════════════════════════════════════════════════════

def run_scheduler():
    """启动阻塞式调度器。"""
    init_db()

    scheduler = BlockingScheduler()
    trigger = CronTrigger(
        day=SCHEDULE["day"],
        hour=SCHEDULE["hour"],
        minute=SCHEDULE["minute"],
        timezone="Asia/Shanghai",
    )

    scheduler.add_job(
        fetch_and_store,
        trigger=trigger,
        id="coal_production_fetch",
        name="抓取原煤产量数据",
        misfire_grace_time=3600,  # 错过执行时间后1小时内仍执行
    )

    logger.info("=" * 60)
    logger.info("定时任务调度器已启动")
    logger.info(f"下次执行: 每月 {SCHEDULE['day']} 日 {SCHEDULE['hour']:02d}:{SCHEDULE['minute']:02d}")
    logger.info(f"时区: Asia/Shanghai")
    logger.info(f"指标编码: {NBS_API['zb_code']}")
    logger.info("=" * 60)

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("调度器已手动停止")
    except Exception as e:
        logger.error("调度器异常: %s", e)
        raise


# ═══════════════════════════════════════════════════════════════
# 命令行入口
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    args = sys.argv[1:]

    if "--now" in args or "--test" in args:
        # 立即执行一次
        init_db()
        fetch_and_store()
        if "--test" not in args:
            # --now 还启动调度器，--test 只执行一次
            logger.info("--now 模式: 执行完成，继续启动调度器...")
            run_scheduler()
    else:
        run_scheduler()
