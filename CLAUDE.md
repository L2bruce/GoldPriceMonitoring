# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

京东浙商银行积存金实时价格监控工具。在 macOS 菜单栏显示实时金价，支持走势图和价格越界告警。

## Environment

```bash
source .venv/bin/activate
```

- **Python version:** 3.9.6
- **Virtual environment:** `.venv/`
- **IDE:** PyCharm (`.idea/` directory)

## Running the App

```bash
python main.py              # 启动菜单栏应用
python main.py --debug      # 调试模式（输出详细日志）
python init_history.py      # 一次性初始化：导入近30个交易日金价日线估算数据
python generate_icon.py     # 按 macOS 规范生成应用图标（824x824主体+阴影）
./build_app.sh              # 打包 .app 并部署到 ~/Applications/（含 LSUIElement 隐藏 Dock 图标）
```

打包产物在 `dist/GoldPrice.app`（部署脚本会拷贝到 `~/Applications/GoldPrice.app`），
双击即可启动。**打包必须用 `./build_app.sh`**：PyInstaller 默认不写 `LSUIElement`，
少了它 app 会在 Dock 显示图标（这是普通应用模式，不是菜单栏插件模式）。
数据/配置独立存放在 `~/.goldprice/`，与 app 位置无关。

## Architecture

```
main.py                         # 入口：初始化并启动 rumps 菜单栏应用
gold_price/
├── app.py                      # rumps 菜单栏应用主体（定时轮询、菜单交互）
├── fetcher.py                  # API 数据获取（主 + 备用接口，自动降级）
├── config.py                   # 配置管理（价格范围、刷新间隔、告警周期、显示样式、通知开关）
├── storage.py                  # JSON 价格历史存储（按日期分组，自动清理）
├── alerter.py                  # 价格越界检测 + macOS osascript 弹窗通知（支持冷却周期）
├── chart.py                    # Plotly 浏览器图表：本进程内本地 HTTP 服务线程
└── __init__.py
~/.goldprice/                   # 运行时数据（用户目录，打包后不变）
├── config.json                 # 用户配置
├── price_history.json          # 价格历史（按日期分组）
├── chart_data.json             # 图表数据（app 每轮询周期刷新）
└── .pid                        # 单实例锁
```

**图表架构**：Plotly 浏览器方案，本地 HTTP 服务（127.0.0.1:8787-8799 找空闲端口）以**线程**运行在主进程内，页面 JS 每 5 秒轮询 `/data` 刷新。浏览器内嵌 plotly.min.js 取自 `plotly/package_data/`。不再有子进程（打包后 bundle 内无法启动独立脚本）。

## Key API

- **Primary**: `POST https://api.jdjygold.com/gw2/generic/jrm/h5/m/stdLatestPrice`
  - Body: `{"reqData": {"productSku": "1961543816"}}`
  - Returns: price, yesterdayPrice, upAndDownRate, upAndDownAmt, time
- **Fallback**: `POST https://api.jdjygold.com/gw/generic/hj/h5/m/latestPrice`
- SKU `1961543816` = 浙商银行积存金
- **历史估算源**（init_history.py）：新浪财经沪金期货日线
  `https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_=/InnerFuturesNewService.getDailyKLine?symbol=AU0`
  （JSONP，取最后 30 条日线，每天生成 4 个点，标记 `"source": "estimate"`）

## Chart JS 关键行为（勿回退）

- x 轴值用 ISO 格式 `日期T时间`（Safari 能解析；带空格的格式会解析成 Invalid Date）
- **视图保持**：rangeselector 无显式 range 时 Plotly 默认用最后一个按钮（30天）。
  当前方案：`lastRange` 记录用户视图（监听 `plotly_relayout`），刷新时显式传回
  `xaxis.range` + `xaxis.autorange=false`。改回"刷新不传 range"会复现跳 30 天 bug。
- `plotly_relayout` 有两种事件形式：拖拽/滚轮产生 `xaxis.range` 数组，点 rangeselector
  按钮产生 `xaxis.range[0]`/`xaxis.range[1]` 键。必须两种都记录（含双击重置的
  `xaxis.autorange` 分支），否则点完按钮后 5 秒刷新会把视图弹回旧范围。
- `dragmode:'pan'` + `yaxis.fixedrange:true`：拖拽=平移、滚轮只缩放 x 轴（时间）。
  默认 zoom 模式鼠标一拖一放会把折线缩出视图范围（框没框住折线就"看不见了"）。
- 菜单只有"📈 打开走势图"一项（用户关闭页面 = 自己关掉浏览器标签页，不做菜单）。
  点击"打开"必定重新弹出页面（每次新开浏览器标签页并激活）：服务没在跑则
  启动服务，在跑则刷新数据后直接弹页。不要改回 toggle（会复现"需要点击两次
  才能弹出页面"），也不要加"关闭走势图"菜单项。

## Python 3.9 Constraints

- Must use `Optional[X]` / `List[X]` / `Dict[K,V]` from `typing` (not `X | None` / `list[X]` / `dict[K,V]`)
- f-string expressions cannot contain backslashes
