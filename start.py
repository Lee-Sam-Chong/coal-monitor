"""
统一启动入口
=============
支持 Web 服务、数据抓取、指标探索、种子数据等多种模式。

用法:
  python start.py serve       # 启动 Web 服务器（默认）
  python start.py fetch       # 立即抓取一次数据
  python start.py discover    # 探索指标树查找原煤产量编码
  python start.py seed        # 生成并导入示例数据
  python start.py scheduler   # 启动定时任务调度器
  python start.py init        # 初始化数据库

环境变量:
  PORT       Web 服务端口号（默认 5000）
  HOST       Web 服务地址（默认 0.0.0.0）
"""

import sys
import os
import logging
import io

# 处理 Windows 终端的 GBK 编码问题
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 确保项目根目录在 Python 路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import LOG_CONFIG

# 配置日志
logging.basicConfig(
    level=getattr(logging, LOG_CONFIG["level"]),
    format=LOG_CONFIG["format"],
    datefmt=LOG_CONFIG["date_format"],
)
logger = logging.getLogger("start")

# 抑制 SSL 警告
import warnings
warnings.filterwarnings("ignore", message="Unverified HTTPS request")


def cmd_serve():
    """启动 Web 服务。"""
    from config import SERVER
    # 允许通过环境变量覆盖端口
    port = int(os.environ.get("PORT", SERVER["port"]))
    host = os.environ.get("HOST", SERVER["host"])

    # 导入 app 会触发数据初始化
    from app import app, initialize_data

    with app.app_context():
        initialize_data()

    logger.info("=" * 60)
    logger.info("🌐 Web 服务启动")
    logger.info(f"   地址: http://localhost:{port}")
    logger.info(f"   地区: {host}")
    logger.info("=" * 60)

    app.run(host=host, port=port, debug=SERVER["debug"])


def cmd_fetch():
    """立即抓取一次数据并存入数据库。"""
    from models import init_db, bulk_upsert
    from fetcher import safe_fetch

    init_db()
    logger.info("开始抓取数据...")
    df = safe_fetch()
    if df is None or df.empty:
        logger.error("未获取到数据")
        sys.exit(1)

    records = [(row["region"], row["year_month"], row["output"])
               for _, row in df.iterrows()]
    count = bulk_upsert(records)
    logger.info("✅ 成功导入 %d 条记录", count)
    print(f"\n共抓取 {len(df)} 条数据，新增 {count} 条")
    print(df[["region", "year_month", "output"]].to_string(index=False))


def cmd_discover():
    """探索指标树，查找原煤产量编码。"""
    from fetcher import discover_indicators, find_production_code

    print("=" * 60)
    print("🔍 探索国家统计局指标树")
    print("=" * 60)

    # 先尝试精确查找
    code = find_production_code()
    if code:
        print(f"\n✅ 自动找到指标编码: {code}")
    else:
        print("\n⚠️  未自动找到，请使用关键词搜索:")

    keyword = input("\n输入搜索关键词（如 原煤、煤炭、能源）直接回车以查看全部: ").strip()
    if not keyword:
        keyword = None

    results = discover_indicators(keyword, max_depth=3)
    print(f"\n找到 {len(results)} 个节点:")
    for r in results:
        marker = "📁" if r["is_parent"] else "📄"
        print(f"  {marker} {r['id']:15s} {r['name']}")

    print("\n💡 提示: 找到正确的指标编码后，请更新 config.py 中的 zb_code")


def cmd_seed():
    """生成并导入示例数据。"""
    from models import init_db, bulk_upsert
    from fetcher import generate_seed_data

    init_db()
    print("🌱 生成示例数据...")
    df = generate_seed_data()
    records = [(row["region"], row["year_month"], row["output"])
               for _, row in df.iterrows()]
    count = bulk_upsert(records)
    print(f"✅ 成功导入 {count} 条示例数据")
    print(f"   共 {df['region'].nunique()} 个地区，时间范围: {df['year_month'].min()} ~ {df['year_month'].max()}")


def cmd_scheduler():
    """启动定时任务调度器。"""
    from scheduler import run_scheduler
    logger.info("⏰ 启动定时任务调度器...")
    run_scheduler()


def cmd_init():
    """初始化数据库。"""
    from models import init_db, get_statistics
    init_db()
    stats = get_statistics()
    print(f"✅ 数据库初始化完成")
    print(f"   路径: {os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'coal_production.db')}")
    print(f"   当前记录: {stats['record_count']} 条, {stats['region_count']} 个地区")


def cmd_info():
    """显示系统信息。"""
    from config import NBS_API, SCHEDULE, SERVER, PROVINCE_CODES
    from models import get_statistics

    print("=" * 60)
    print("⛏️  中国煤炭月度产量数据监测系统")
    print("=" * 60)
    print(f"\n📁 项目路径: {os.path.dirname(os.path.abspath(__file__))}")

    print(f"\n🔧 配置信息:")
    print(f"   API 地址: {NBS_API['base_url']}")
    print(f"   数据库编码: {NBS_API['dbcode']}")
    print(f"   指标编码: {NBS_API['zb_code']}")
    print(f"   地区数量: {len(PROVINCE_CODES)} 个")

    print(f"\n⏰ 调度配置:")
    print(f"   每月 {SCHEDULE['day']} 日 {SCHEDULE['hour']:02d}:{SCHEDULE['minute']:02d} 执行")

    print(f"\n🌐 服务配置:")
    print(f"   http://localhost:{SERVER['port']}")

    stats = get_statistics()
    if stats["record_count"] > 0:
        print(f"\n📊 数据状态: ✅ 已有数据")
        print(f"   记录数: {stats['record_count']}")
        print(f"   地区数: {stats['region_count']}")
        print(f"   时间范围: {stats['min_ym']} ~ {stats['max_ym']}")
    else:
        print(f"\n📊 数据状态: ❌ 数据库为空")
        print(f"   运行 'python start.py seed' 生成示例数据")
        print(f"   或 'python start.py fetch' 从 API 获取")

    print("\n📋 可用命令:")
    print("   python start.py serve      启动 Web 服务")
    print("   python start.py fetch      抓取数据")
    print("   python start.py discover   探索指标树")
    print("   python start.py seed       生成示例数据")
    print("   python start.py scheduler  启动定时任务")
    print("   python start.py init       初始化数据库")
    print("=" * 60)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"

    commands = {
        "serve": cmd_serve,
        "fetch": cmd_fetch,
        "discover": cmd_discover,
        "seed": cmd_seed,
        "scheduler": cmd_scheduler,
        "init": cmd_init,
        "info": cmd_info,
    }

    if cmd in commands:
        commands[cmd]()
    else:
        print(f"未知命令: {cmd}")
        print(f"可用命令: {', '.join(commands.keys())}")
        sys.exit(1)
