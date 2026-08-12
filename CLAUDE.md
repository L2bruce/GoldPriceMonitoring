# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

京东浙商银行积存金实时价格监控工具。macOS 菜单栏 / Windows 系统托盘显示实时金价，支持走势图和价格越界告警。macOS 版基于 rumps，Windows 版基于 pystray，共享业务逻辑（`platform_ui.py` 隔离平台差异）。

## Environment

```bash
source .venv/bin/activate
```

- **Python version:** 3.9.6
- **Virtual environment:** `.venv/`
- **IDE:** PyCharm (`.idea/` directory)
- **Windows 依赖安装：** `pip install -r requirements-win.txt`（不要装 rumps，其依赖 pyobjc 在 Windows 不可安装）

## Running the App

```bash
python main.py              # 启动（macOS 菜单栏 / Windows 托盘，自动分派）
python main.py --debug      # 调试模式（输出详细日志）
python init_history.py      # 一次性初始化：导入近30个交易日金价日线估算数据
pyinstaller --noconfirm --clean --windowed --name GoldPrice \
  --icon build/icon.icns --collect-data plotly \
  --osx-bundle-identifier com.bruce.goldprice main.py   # macOS 打包 .app
pyinstaller --noconfirm --clean --noconsole --name GoldPrice \
  --icon build/icon.ico --collect-data plotly main.py    # Windows 打包 .exe（需 .ico 图标）
```

macOS 打包产物在 `dist/GoldPrice.app`，Windows 在 `dist/GoldPrice.exe`，双击即可启动。数据/配置独立存放在 `~/.goldprice/`，与程序位置无关。

**Windows 打包注意**：PyInstaller 不支持跨平台编译，必须在 Windows 机器上执行。仓库里已备好：
- `build_win.bat` — 一键打包（自动建 venv、装依赖、打单文件 exe）
- `installer_win.iss` — Inno Setup 安装包脚本（可选，需装 Inno Setup，产出 setup.exe 带桌面/开始菜单快捷方式）
- `build/icon.ico` — Windows 图标（由 icon_1024.png 生成，多尺寸）
- `WINDOWS.md` — 绿色版打包 + 运行 + 常见问题完整指南

## Architecture

```
main.py                         # 入口：平台路由 + 单实例锁
gold_price/
├── app.py                      # macOS 菜单栏应用（rumps，定时轮询、菜单交互）
├── win_app.py                  # Windows 托盘应用（pystray，功能与 macOS 版对齐）
├── platform_ui.py              # 平台抽象：输入对话框/告警弹窗/进程检测（mac: osascript, win: tkinter/ctypes）
├── fetcher.py                  # API 数据获取（主 + 备用接口，自动降级）
├── config.py                   # 配置管理（价格范围、刷新间隔、通知开关）
├── storage.py                  # JSON 价格历史存储（按日期分组，自动清理）
├── alerter.py                  # 价格越界检测 + platform_ui 弹窗通知
├── chart.py                    # Plotly 浏览器图表：本进程内本地 HTTP 服务线程
└── __init__.py
~/.goldprice/                   # 运行时数据（用户目录，打包后不变）
├── config.json                 # 用户配置
├── price_history.json          # 价格历史（按日期分组）
├── chart_data.json             # 图表数据（app 每轮询周期刷新）
└── .pid                        # 单实例锁
```

**平台适配规则**：
- 新增界面交互一律走 `platform_ui.py` 的函数，不在业务代码里写 osascript/AppKit
- `win_app.py` 不 import `app.py`（rumps 在 Windows 不可用），常量如需共享放 `platform_ui.py` 或各自定义
- macOS 托盘/菜单 emoji 正常；Windows 托盘菜单不用 emoji
- `_BrightMenuItem` 的 AppKit 上色逻辑有 `sys.platform != "darwin"` guard

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

## Python 3.9 Constraints

- Must use `Optional[X]` / `List[X]` / `Dict[K,V]` from `typing` (not `X | None` / `list[X]` / `dict[K,V]`)
- f-string expressions cannot contain backslashes
