# ⛏️ 中国煤炭月度产量数据监测系统

China Coal Monthly Production Monitor — 从国家统计局自动获取原煤产量数据，通过 Web 图表可视化展示。

## 功能特点

- 📊 **数据可视化**：折线图展示月度产量趋势，支持全国及各省切换
- 🤖 **自动抓取**：定时从国家统计局 API 获取最新数据
- 💾 **本地存储**：SQLite 数据库，数据持久化，避免重复抓取
- 🌐 **Web 界面**：响应式设计，支持桌面和移动端
- 🔄 **自动更新**：每月 16 日自动抓取次月中旬发布的最新数据

## 快速开始

### 环境要求

- Python 3.8+
- pip（Python 包管理工具）

### 一键启动（Windows）

双击 `run.bat`，或在命令行中运行：

```bash
run.bat
```

### 手动启动

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 生成示例数据（首次运行，API 不可用时使用）
python start.py seed

# 3. 启动 Web 服务
python start.py serve
```

打开浏览器访问：**http://localhost:5000**

## 命令参考

| 命令 | 说明 |
|------|------|
| `python start.py serve` | 启动 Web 服务器（默认命令） |
| `python start.py fetch` | 立即从国家统计局 API 抓取一次数据 |
| `python start.py discover` | 探索 NBS 指标树，查找原煤产量编码 |
| `python start.py seed` | 生成并导入示例数据（API 不可用时的演示） |
| `python start.py scheduler` | 启动定时任务调度器（每月 16 日执行） |
| `python start.py init` | 初始化数据库 |
| `python start.py info` | 显示系统信息 |

## 项目结构

```
coal-production-monitor/
├── config.py          # 配置文件（API 参数、省份代码、调度设置）
├── fetcher.py         # NBS API 客户端（抓取、解析、指标探索）
├── models.py          # 数据库操作层（SQLite）
├── scheduler.py       # 定时任务调度器（APScheduler）
├── app.py             # Flask Web 应用（API + 前端路由）
├── templates/
│   └── index.html     # 前端单页面（Chart.js + Tailwind CSS）
├── data/
│   └── coal_production.db  # SQLite 数据库（自动创建）
├── start.py           # 统一启动入口
├── run.bat            # Windows 一键启动脚本
├── requirements.txt   # Python 依赖
└── README.md          # 本文档
```

## API 接口

| 接口 | 说明 |
|------|------|
| `GET /api/production?region=全国&months=12` | 获取产量数据 |
| `GET /api/production/regions` | 获取地区列表 |
| `GET /api/production/table?region=全国&months=6` | 获取数据表格（含环比） |
| `GET /api/production/statistics` | 获取数据统计 |
| `GET /api/production/refresh` | 手动触发数据刷新 |
| `GET /api/production/seed` | 生成示例数据 |

### API 返回示例

```json
GET /api/production?region=全国&months=12

{
  "region": "全国",
  "months": 12,
  "data": [
    {"year_month": "2025-07", "output": 38627.5},
    {"year_month": "2025-08", "output": 39215.3},
    ...
  ]
}
```

## 数据源配置

### 国家统计局 API

系统通过国家统计局官方数据平台 `data.stats.gov.cn` 的 JSON API 获取数据。

主要参数（在 `config.py` 中配置）：

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `dbcode` | 数据库编码 | `fsyd`（分省月度数据） |
| `zb_code` | 指标编码 | `A02010101`（需验证） |
| `rowcode` | 行维度 | `reg`（地区） |
| `colcode` | 列维度 | `sj`（时间） |

### 查找正确的指标编码

如果默认指标编码 `A02010101` 不返回数据，可以使用指标探索工具：

```bash
python start.py discover
```

此命令会递归探索 NBS 的指标树，查找包含"原煤"的指标编码。找到后更新 `config.py` 中的 `zb_code`。

也可以手动在浏览器中探索：
```
https://data.stats.gov.cn/easyquery.htm?m=getTree&id=A0&dbcode=fsyd&wdcode=zb
```

## 数据说明

- **数据来源**：国家统计局 (data.stats.gov.cn)
- **数据指标**：原煤月度产量（万吨）
- **更新频率**：月度数据，通常在次月中旬发布
- **覆盖范围**：全国 + 各省（自治区、直辖市）

### 示例数据

如果无法访问国家统计局 API，系统会自动生成基于历史数据的合理模拟值用于演示。生成的数据包含：
- 季节性波动（冬季用煤高峰）
- 近年增产趋势
- 主要产煤省份（山西、内蒙古、陕西等）产量差异

## 定时任务

使用 APScheduler 实现定时抓取，配置在 `config.py` 中：

- **默认时间**：每月 16 日凌晨 2:00
- **时区**：Asia/Shanghai
- **自动去重**：已有记录不会重复插入
- **运行方式**：`python start.py scheduler`

## 常见问题

**Q: 无法连接国家统计局 API？**
A: NBS 网站可能有反爬限制。首次使用建议运行 `python start.py seed` 生成示例数据。如果 API 可连接但返回 403，系统会自动重试并更换 User-Agent。

**Q: 图表没有数据？**
A: 运行 `python start.py seed` 导入示例数据，然后刷新页面。

**Q: 如何修改端口号？**
A: 设置环境变量 `PORT=8080 python start.py serve`，或修改 `config.py` 中的 `SERVER.port`。

## License

MIT
