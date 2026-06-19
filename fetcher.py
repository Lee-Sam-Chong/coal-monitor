"""
数据抓取器 - 国家统计局 API 客户端
====================================
负责从 data.stats.gov.cn 获取原煤产量数据。

功能：
1. 探索指标树，自动发现 zb 编码
2. 按年月范围获取分省月度数据
3. 指数退避重试 + 随机延时
4. 解析非标准 JSONP/JSON 响应
5. 数据验证和格式化

API 参考：
  GET easyquery.htm?m=QueryData&dbcode=fsyd&rowcode=reg&colcode=sj
  &wds=[]&dfwds=[{"wdcode":"zb","valuecode":"A02010101"},{"wdcode":"sj","valuecode":"202401"}]
"""

import re
import json
import time
import random
import logging
from datetime import datetime, timedelta

import requests
import pandas as pd

from config import NBS_API, FALLBACK_API, PROVINCE_CODES, PROVINCE_NAMES, USE_SEED_DATA_IF_API_FAILS

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 1. HTTP 请求工具
# ═══════════════════════════════════════════════════════════════

def _make_session():
    """创建一个配置好的 requests Session。"""
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://data.stats.gov.cn/easyquery.htm?cn=E0103",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
    })
    session.verify = False  # NBS 证书有时不被信任
    return session


def _request_with_retry(url, params, max_retries=None, base_delay=None):
    """
    带指数退避重试的 GET 请求。

    Args:
        url: 请求 URL
        params: 查询参数 dict
        max_retries: 最大重试次数（默认取配置值）
        base_delay: 初始延迟秒数（默认取配置值）

    Returns:
        requests.Response 或 None（全部失败时）
    """
    cfg = NBS_API
    max_retries = max_retries if max_retries is not None else cfg["retries"]
    base_delay = base_delay if base_delay is not None else cfg["retry_delay_base"]

    session = _make_session()

    for attempt in range(1 + max_retries):
        try:
            if attempt > 0:
                delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 1)
                logger.info("重试 #%d，等待 %.1f 秒后重试...", attempt, delay)
                time.sleep(delay)

            response = session.get(url, params=params, timeout=cfg["timeout"])
            logger.debug("请求: %s?%s", url, response.url.split("?", 1)[1][:100])

            if response.status_code == 200:
                return response
            elif response.status_code == 403:
                logger.warning("403 Forbidden (尝试 #%d)，可能被反爬拦截", attempt + 1)
                if attempt < max_retries:
                    # 换一个 UA 和 Referer 再试
                    session.headers.update({
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            f"Chrome/{random.choice([118,119,120,121])}.0.0.0 Safari/537.36"
                        ),
                        "Referer": f"https://data.stats.gov.cn/easyquery.htm?cn=E0103&zb={random.randint(100000,999999)}",
                    })
                    time.sleep(random.uniform(3, 6))
            else:
                logger.warning("HTTP %s (尝试 #%d)", response.status_code, attempt + 1)

        except requests.exceptions.Timeout:
            logger.warning("请求超时 (尝试 #%d)", attempt + 1)
        except requests.exceptions.ConnectionError as e:
            logger.warning("连接错误: %s (尝试 #%d)", e, attempt + 1)
        except Exception as e:
            logger.error("请求异常: %s (尝试 #%d)", e, attempt + 1)

    logger.error("请求全部失败: %s?%s", url, params)
    return None


# ═══════════════════════════════════════════════════════════════
# 2. 指标树探索
# ═══════════════════════════════════════════════════════════════

def fetch_indicator_tree(node_id="A0", dbcode=None):
    """
    获取指定节点下的指标树。

    Args:
        node_id: 节点 ID，从 "A0" 根节点开始
        dbcode: 数据库编码，默认使用配置值

    Returns:
        list[dict]: 子节点列表，每个节点含 id, name, isParent 等字段
                    None 表示请求失败
    """
    cfg = NBS_API
    dbcode = dbcode or cfg["dbcode"]
    url = cfg["base_url"]

    params = {
        "id": node_id,
        "dbcode": dbcode,
        "wdcode": "zb",
        "m": "getTree",
    }

    response = _request_with_retry(url, params)
    if response is None:
        return None

    try:
        data = response.json()
        if isinstance(data, list):
            return data
        else:
            logger.warning("指标树返回非列表格式: %s", type(data))
            return []
    except json.JSONDecodeError as e:
        logger.error("指标树 JSON 解析失败: %s", e)
        logger.debug("原始响应前200字符: %s", response.text[:200])
        return []


def discover_indicators(keyword=None, max_depth=3, dbcode=None):
    """
    递归探索指标树，查找匹配的指标编码。

    用法:
        # 查找所有包含"原煤"的指标
        results = discover_indicators("原煤")

        # 查看工业分类下所有指标
        results = discover_indicators("工业")

    Args:
        keyword: 搜索关键词（如"原煤"、"煤炭"），None 则返回全部
        max_depth: 最大递归深度
        dbcode: 数据库编码

    Returns:
        list[dict]: [{"id": "A020101", "name": "原煤产量", "path": "A0 > A02 > ..."}, ...]
    """
    results = []

    def _walk(node_id, path, depth):
        if depth > max_depth:
            return
        children = fetch_indicator_tree(node_id, dbcode)
        if not children:
            return
        for child in children:
            child_id = child.get("id", "")
            child_name = child.get("name", "")
            child_path = f"{path} > {child_name}" if path else child_name

            # 关键词匹配
            if keyword and keyword in child_name:
                results.append({
                    "id": child_id,
                    "name": child_name,
                    "path": child_path,
                    "is_parent": child.get("isParent", False),
                })
                logger.info("🔍 找到匹配: %s (%s)", child_path, child_id)

            # 无关键词时也记录（用于遍历）
            if not keyword:
                results.append({
                    "id": child_id,
                    "name": child_name,
                    "path": child_path,
                    "is_parent": child.get("isParent", False),
                })

            # 如果有子节点，继续递归
            if child.get("isParent", False):
                _walk(child_id, child_path, depth + 1)

    logger.info("开始探索指标树 (dbcode=%s, keyword=%s, max_depth=%d)",
                dbcode or NBS_API["dbcode"], keyword, max_depth)
    _walk("A0", "", 0)
    logger.info("指标树探索完成，共找到 %d 个节点", len(results))
    return results


def find_production_code(dbcode=None):
    """
    自动查找原煤产量对应的 zb 编码。

    Returns:
        str 或 None: 找到的指标编码
    """
    # 先精确搜索
    results = discover_indicators("原煤产量", max_depth=4, dbcode=dbcode)
    if results:
        # 优先选择非父节点的编码（叶节点）
        for r in results:
            if not r["is_parent"]:
                logger.info("✅ 找到原煤产量编码: %s (%s)", r["id"], r["path"])
                return r["id"]
        # 如果都是父节点，返回第一个
        logger.info("✅ 找到原煤产量编码(父节点): %s (%s)", results[0]["id"], results[0]["path"])
        return results[0]["id"]

    # 再宽泛搜索
    results = discover_indicators("原煤", max_depth=5, dbcode=dbcode)
    if results:
        for r in results:
            if not r["is_parent"]:
                logger.info("✅ 找到原煤相关编码: %s (%s)", r["id"], r["path"])
                return r["id"]

    logger.warning("❌ 未找到原煤产量编码，请手动在 config.py 中设置 zb_code")
    return None


# ═══════════════════════════════════════════════════════════════
# 3. 数据抓取核心
# ═══════════════════════════════════════════════════════════════

def _parse_nbs_response(response):
    """
    解析 NBS API 返回的 JSON 数据。

    NBS 返回格式:
    {
        "returncode": "200",
        "returndata": {
            "datanodes": [
                {"code": "zb.A02010101.reg.110000.sj.202401",
                 "data": {"data": "386.72", "hasdata": true}},
                ...
            ],
            "wdnodes": [
                {"wdcode": "reg", "nodes": [
                    {"code": "110000", "name": "北京市"},
                    ...
                ]},
                {"wdcode": "sj", "nodes": [
                    {"code": "202401", "name": "2024年1月"},
                    ...
                ]}
            ]
        }
    }

    节点编码格式: zb.{zb_code}.reg.{reg_code}.sj.{time_code}
    我们需要从 wdnodes 中找出 reg 和 sj 的名称映射。

    Returns:
        pd.DataFrame 或 None
    """
    try:
        resp_json = response.json()
    except json.JSONDecodeError:
        # 尝试处理 JSONP 格式
        text = response.text.strip()
        if text.startswith("(") and text.endswith(")"):
            text = text[1:-1]
        elif "callback(" in text:
            text = re.search(r"callback\((.+)\)", text)
            if text:
                text = text.group(1)
        try:
            resp_json = json.loads(text)
        except (json.JSONDecodeError, AttributeError):
            logger.error("无法解析响应 JSON: %s", response.text[:200])
            return None

    returncode = resp_json.get("returncode")
    if returncode != "200":
        logger.warning("API 返回异常 returncode=%s: %s",
                       returncode, resp_json.get("returndata", {}).get("message", ""))
        return None

    returndata = resp_json.get("returndata", {})
    datanodes = returndata.get("datanodes", [])
    if not datanodes:
        logger.warning("API 返回空数据 (datanodes 为空)")
        return None

    # ── 构建维度名称映射 ──
    # reg 代码 → 地区名称
    reg_map = {}
    # sj 代码 → 年月标识
    sj_map = {}

    for wdnode in returndata.get("wdnodes", []):
        wdcode = wdnode.get("wdcode")
        for node in wdnode.get("nodes", []):
            code = node.get("code", "")
            name = node.get("name", "")
            if wdcode == "reg":
                reg_map[code] = name
            elif wdcode == "sj":
                sj_map[code] = name

    # ── 解析数据节点 ──
    records = []
    for node in datanodes:
        code_str = node.get("code", "")
        data_obj = node.get("data", {})

        # 无数据则跳过
        if not data_obj.get("hasdata", False):
            continue

        value = data_obj.get("data")
        if value is None or value == "" or value == "…":
            continue

        try:
            value = float(value)
        except (ValueError, TypeError):
            continue

        # 从 code 中提取 reg_code 和 sj_code
        # 格式: zb.{zb}.reg.{reg_code}.sj.{sj_code}
        parts = code_str.split(".")
        reg_code = None
        sj_code = None

        for i, part in enumerate(parts):
            if part == "reg" and i + 1 < len(parts):
                reg_code = parts[i + 1]
            elif part == "sj" and i + 1 < len(parts):
                sj_code = parts[i + 1]

        if not reg_code or not sj_code:
            continue

        # 确定地区名称
        region_name = reg_map.get(reg_code, "")
        if not region_name:
            # 尝试从配置的 PROVINCE_CODES 中查找
            region_name = PROVINCE_CODES.get(reg_code, reg_code)

        # 将 NBS 时间码转为 YYYY-MM 格式
        # NBS 时间格式: "202401" (年月) 或 "2024A" (月度字母编码)
        year_month = _nbs_time_to_ym(sj_code)

        if region_name and year_month:
            records.append({
                "region": region_name,
                "year_month": year_month,
                "output": value,
            })

    if not records:
        logger.warning("解析后无有效数据记录")
        return None

    df = pd.DataFrame(records)
    # 去重（某些 API 可能返回重复节点）
    df = df.drop_duplicates(subset=["region", "year_month"])
    df = df.sort_values(["region", "year_month"]).reset_index(drop=True)

    logger.info("成功解析 %d 条数据记录 (含 %d 个地区, 时间范围: %s ~ %s)",
                len(df), df["region"].nunique(),
                df["year_month"].min(), df["year_month"].max())
    return df


def _nbs_time_to_ym(sj_code):
    """
    将 NBS 时间编码转为 YYYY-MM 格式。

    NBS 时间编码有多种格式:
    - "202401"   → 年月数字
    - "2024M01"  → 带 M 前缀
    - "2024A"    → 字母月度编码 (A=1月, B=2月, ...)
    - "2024"     → 年度
    """
    if not sj_code:
        return None

    sj_code = str(sj_code)

    # 格式1: "202401" (6位数字)
    if len(sj_code) == 6 and sj_code.isdigit():
        year = sj_code[:4]
        month = sj_code[4:6]
        return f"{year}-{month}"

    # 格式2: "2024M01" (7-8位，含 M)
    m = re.match(r"(\d{4})M(\d{2})", sj_code)
    if m:
        return f"{m.group(1)}-{m.group(2)}"

    # 格式3: "2024A" → 字母月度 (A=1, B=2, ..., L=12)
    m = re.match(r"(\d{4})([A-L])", sj_code)
    if m:
        year = m.group(1)
        month_num = ord(m.group(2)) - ord("A") + 1
        return f"{year}-{month_num:02d}"

    # 格式4: 纯年度 "2024"
    if len(sj_code) == 4 and sj_code.isdigit():
        logger.debug("时间编码 '%s' 为年度格式，跳过", sj_code)
        return None  # 月度数据不处理年度

    logger.warning("无法解析时间编码: %s", sj_code)
    return None


def _gen_time_codes(start_ym, end_ym):
    """
    生成 NBS API 需要的时间码列表。

    NBS API 支持两种时间参数：
    1. "LAST12" / "LAST6" — 最近 N 期
    2. 具体时间码 "202401", "202402", ...

    Args:
        start_ym: "YYYY-MM" 格式
        end_ym: "YYYY-MM" 格式

    Returns:
        str: 如 "202401,202402,...,202406" 或 "LAST12"
    """
    try:
        start = datetime.strptime(start_ym, "%Y-%m")
        end = datetime.strptime(end_ym, "%Y-%m")
    except ValueError:
        return "LAST12"

    codes = []
    current = start
    while current <= end:
        codes.append(current.strftime("%Y%m"))
        # 下个月
        year = current.year + (current.month // 12)
        month = (current.month % 12) + 1
        current = current.replace(year=year, month=month)

    return ",".join(codes) if codes else "LAST12"


def fetch_coal_production(start_year_month=None, end_year_month=None, zb_code=None):
    """
    从国家统计局 API 获取原煤产量数据。

    Args:
        start_year_month: 起始年月 "YYYY-MM"，默认最近 24 个月
        end_year_month: 结束年月 "YYYY-MM"，默认当前月
        zb_code: 指标编码，默认使用 config 中的设置

    Returns:
        pd.DataFrame | None:
            columns: region, year_month, output
    """
    cfg = NBS_API
    zb_code = zb_code or cfg["zb_code"]

    now = datetime.now()
    if end_year_month is None:
        end_ym = f"{now.year}-{now.month:02d}"
    else:
        end_ym = end_year_month

    if start_year_month is None:
        # 默认抓取最近 24 个月
        start = now - timedelta(days=730)
        start_ym = f"{start.year}-{start.month:02d}"
    else:
        start_ym = start_year_month

    # 生成时间参数
    time_codes = _gen_time_codes(start_ym, end_ym)
    logger.info("开始抓取原煤产量数据: %s ~ %s, zb=%s, 时间码=%s",
                start_ym, end_ym, zb_code, time_codes)

    url = cfg["base_url"]
    params = {
        "m": "QueryData",
        "dbcode": cfg["dbcode"],
        "rowcode": cfg["rowcode"],
        "colcode": cfg["colcode"],
        "wds": "[]",
        "dfwds": json.dumps([
            {"wdcode": "zb", "valuecode": zb_code},
            {"wdcode": "sj", "valuecode": time_codes},
        ]),
    }

    # 添加随机延时
    delay = random.uniform(*cfg["random_delay"])
    logger.debug("随机延时 %.2f 秒", delay)
    time.sleep(delay)

    # 发送请求
    response = _request_with_retry(url, params)
    if response is None:
        logger.warning("主要 API 请求失败")
        return _try_fallback(start_ym, end_ym, zb_code)

    # 解析响应
    df = _parse_nbs_response(response)
    if df is not None and not df.empty:
        return df

    logger.warning("主要 API 返回数据为空或解析失败")
    return _try_fallback(start_ym, end_ym, zb_code)


def _try_fallback(start_ym, end_ym, zb_code):
    """尝试使用备用 API 配置抓取。"""
    logger.info("尝试备用 API 配置...")
    cfg = FALLBACK_API

    url = NBS_API["base_url"]
    time_codes = _gen_time_codes(start_ym, end_ym)
    params = {
        "m": "QueryData",
        "dbcode": cfg["dbcode"],
        "rowcode": NBS_API["rowcode"],
        "colcode": NBS_API["colcode"],
        "wds": "[]",
        "dfwds": json.dumps([
            {"wdcode": "zb", "valuecode": cfg["zb_code"]},
            {"wdcode": "sj", "valuecode": time_codes},
        ]),
    }

    response = _request_with_retry(url, params)
    if response is None:
        logger.warning("备用 API 也请求失败")
        return None

    df = _parse_nbs_response(response)
    if df is not None and not df.empty:
        logger.info("备用 API 获取成功: %d 条数据", len(df))
        return df

    logger.warning("备用 API 数据为空")
    return None


# ═══════════════════════════════════════════════════════════════
# 4. 种子数据（API 不可用时的替代数据）
# ═══════════════════════════════════════════════════════════════

def generate_seed_data():
    """
    生成模拟的原煤产量数据用于演示和测试。

    基于国家统计局历史发布数据的大致范围生成合理的模拟值。
    单位：万吨（万吨）

    Returns:
        pd.DataFrame
    """
    import numpy as np

    logger.info("生成示例数据用于演示...")
    np.random.seed(42)

    # 最近36个月的基准值（万吨/月）
    # 全国原煤产量近年大致在 3.5~4.5 亿吨/月 = 35000~45000 万吨/月
    base_output = {
        "全国": 38000,
        "山西省": 10500,
        "内蒙古自治区": 10000,
        "陕西省": 6500,
        "新疆维吾尔自治区": 3500,
        "贵州省": 1200,
        "安徽省": 1000,
        "河南省": 900,
        "山东省": 800,
        "河北省": 700,
        "宁夏回族自治区": 700,
        "黑龙江省": 550,
        "甘肃省": 500,
        "辽宁省": 300,
        "江苏省": 250,
        "湖南省": 250,
        "云南省": 500,
        "四川省": 400,
        "江西省": 200,
        "吉林省": 200,
        "福建省": 150,
        "重庆市": 300,
        "北京市": 0,
        "天津市": 0,
        "上海市": 0,
        "海南省": 0,
        "西藏自治区": 0,
        "广西壮族自治区": 100,
        "湖北省": 100,
        "广东省": 0,
        "浙江省": 0,
        "青海省": 100,
    }

    # 生成时间序列
    now = datetime.now()
    records = []
    for i in range(36):
        ym_date = now.replace(day=1) - timedelta(days=30 * i)
        year_month = ym_date.strftime("%Y-%m")

        for region, base in base_output.items():
            if base == 0:
                # 无煤炭生产的省份，偶尔有极少量
                val = np.random.uniform(0, 5)
            else:
                # 带季节性波动和趋势的模拟值
                season = 1 + 0.15 * np.sin(2 * np.pi * (ym_date.month - 3) / 12)
                trend = 1 + 0.02 * (36 - i) / 36  # 近年整体上升趋势
                noise = np.random.normal(0, base * 0.03)
                val = base * season * trend + noise

            output = round(max(0, val), 2)
            records.append({
                "region": region,
                "year_month": year_month,
                "output": output,
            })

    df = pd.DataFrame(records)
    df = df.sort_values(["region", "year_month"]).reset_index(drop=True)
    logger.info("示例数据生成完成: %d 条记录", len(df))
    return df


# ═══════════════════════════════════════════════════════════════
# 5. 便捷函数
# ═══════════════════════════════════════════════════════════════

def safe_fetch(start_year_month=None, end_year_month=None):
    """
    安全的抓取函数：先尝试 API，API 失败时生成种子数据。

    Returns:
        pd.DataFrame: 包含 region, year_month, output 列
                      API 和种子数据都失败时返回空 DataFrame
    """
    df = fetch_coal_production(start_year_month, end_year_month)

    if df is None or df.empty:
        if USE_SEED_DATA_IF_API_FAILS:
            logger.warning("API 获取失败，使用示例数据代替")
            df = generate_seed_data()
        else:
            logger.error("API 获取失败且不允许使用示例数据")
            return pd.DataFrame(columns=["region", "year_month", "output"])

    return df


# ═══════════════════════════════════════════════════════════════
# 6. 命令行入口
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    # 抑制 requests/urllib3 的 SSL 警告
    import warnings
    warnings.filterwarnings("ignore", message="Unverified HTTPS request")

    if len(sys.argv) >= 2 and sys.argv[1] == "discover":
        keyword = sys.argv[2] if len(sys.argv) >= 3 else None
        results = discover_indicators(keyword, max_depth=4)
        print(f"\n找到 {len(results)} 个匹配节点:")
        for r in results:
            marker = "📁" if r["is_parent"] else "📄"
            print(f"  {marker} {r['id']:15s} {r['name']}")

    elif len(sys.argv) >= 2 and sys.argv[1] == "fetch":
        df = safe_fetch()
        if df is not None and not df.empty:
            print(df.to_string(index=False))
        else:
            print("未获取到数据")

    elif len(sys.argv) >= 2 and sys.argv[1] == "seed":
        df = generate_seed_data()
        print(df.to_string(index=False))
        print(f"\n共 {len(df)} 条记录")

    else:
        print("用法:")
        print("  python fetcher.py discover [关键词]   # 探索指标树")
        print("  python fetcher.py fetch               # 抓取数据")
        print("  python fetcher.py seed                # 生成示例数据")
