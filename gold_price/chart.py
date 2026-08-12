"""价格走势图模块。

Plotly 浏览器渲染方案：本地 HTTP 服务（线程）提供页面和数据，
页面 JS 每 5 秒拉取 /data 刷新。不再使用子进程——
打包成 .app 后 bundle 内无法再启动独立脚本，且线程方案
生命周期与主进程完全同步，无僵尸进程、无端口残留问题。

数据目录在 ~/.goldprice/（打包后 app 位置可变）。
"""

import json
import os
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler

import plotly

DATA_DIR = os.path.expanduser("~/.goldprice")
CHART_DATA_FILE = os.path.join(DATA_DIR, "chart_data.json")
PORT_RANGE = range(8787, 8800)

_PLOTLY_JS_PATH = os.path.join(
    os.path.dirname(plotly.__file__), "package_data", "plotly.min.js"
)

# 当前 HTTP 服务（None = 未打开图表）
_httpd = None

# ── 页面 HTML ──────────────────────────────────────

HTML_PAGE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>浙商积存金 - 近三天走势</title>
<style>
html,body{margin:0;padding:0;height:100%;background:#1e1e2e}
#chart{width:100%;height:100vh}
</style>
</head>
<body>
<div id="chart"></div>
<script src="/plotly.min.js"></script>
<script>
const C = {up:'#a6e3a1', down:'#f38ba8', yellow:'#f9e2af'};
// 当前用户视图范围：刷新时显式传回，防止 Plotly 重新自动计算
// （x 轴无显式 range 时 rangeselector 会默认用最后一个按钮 = 30天）
let lastRange = null;
let gdBound = false;

function baseLayout(title) {
  return {
    title: {text: title, font:{color:'#cdd6f4', size:16}},
    paper_bgcolor:'#1e1e2e', plot_bgcolor:'#1e1e2e',
    font:{color:'#cdd6f4', family:'PingFang SC, Heiti SC, sans-serif'},
    xaxis:{type:'date', gridcolor:'#313244', tickfont:{color:'#a6adc8', size:11},
           rangeselector:{
             bgcolor:'#45475a',
             bordercolor:'#585b70',
             borderwidth:1,
             activecolor:'#6c7086',
             font:{color:'#ffffff', size:12},
             buttons:[
               {count:1,  label:'1天', step:'day', stepmode:'backward'},
               {count:3,  label:'3天', step:'day', stepmode:'backward'},
               {count:7,  label:'7天', step:'day', stepmode:'backward'},
               {count:30, label:'30天', step:'day', stepmode:'backward'}
             ]
           }},
    yaxis:{title:{text:'价格 (元/克)', font:{color:'#cdd6f4'}},
           gridcolor:'#313244', tickfont:{color:'#a6adc8'}},
    margin:{l:60, r:30, t:60, b:50},
    hovermode:'x',
    hoverlabel:{bgcolor:'#313244', bordercolor:'#585b70', font:{color:'#cdd6f4'}},
    showlegend:false
  };
}

function buildTraces(d) {
  const days = (d.days || []).slice().sort((a,b) => a.date < b.date ? -1 : 1);
  const x = [], y = [];
  for (const day of days) {
    for (const r of (day.records || [])) {
      x.push(day.date + 'T' + r.time);  // ISO 格式，Safari 也能正确解析
      y.push(r.price);
    }
  }

  if (!x.length) {
    return {traces: [], layout: baseLayout('浙商积存金 - 等待数据...')};
  }

  const up = y[y.length-1] >= y[0];
  const color = up ? C.up : C.down;
  const trace = {
    x, y, type:'scatter', mode:'lines+markers',
    line:{color, width:1.8}, marker:{size:3, color},
    hovertemplate:'%{x|%m/%d %H:%M:%S}<br>¥%{y:.2f}<extra></extra>'
  };

  const layout = baseLayout(
    '浙商积存金 近三天走势 | ¥' + y[y.length-1].toFixed(2) +
    (up ? ' ↑' : ' ↓')
  );
  if (d.yesterday_price) {
    layout.shapes = [{
      type:'line',
      x0:x[0], x1:x[x.length-1],
      y0:d.yesterday_price, y1:d.yesterday_price,
      line:{color:C.yellow, dash:'dash', width:1}
    }];
  }
  // 视图范围：保留用户当前选择；首次打开默认最近1天
  if (lastRange) {
    layout.xaxis.range = lastRange;
  } else {
    const lastT = new Date(x[x.length-1]).getTime();
    if (!isNaN(lastT)) {
      lastRange = [lastT - 24*3600*1000, lastT + 60*1000];
      layout.xaxis.range = lastRange;
    }
  }
  // 显式指定了 range 就必须关闭 autorange，否则 Plotly 会重新自动计算
  if (layout.xaxis.range) {
    layout.xaxis.autorange = false;
  }
  return {traces:[trace], layout};
}

async function refresh() {
  try {
    const res = await fetch('/data');
    const d = await res.json();
    const {traces, layout} = buildTraces(d);
    await Plotly.react('chart', traces, layout, {
      displayModeBar: false,
      scrollZoom: true,
      responsive: true
    });
    // 监听视图变化（rangeselector 按钮 / 滚轮缩放 / 框选），刷新时保持用户选择
    if (!gdBound) {
      document.getElementById('chart').on('plotly_relayout', (e) => {
        if (Array.isArray(e['xaxis.range'])) lastRange = e['xaxis.range'];
      });
      gdBound = true;
    }
  } catch (e) { console.error(e); }
}

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


# ── 数据写入 ──────────────────────────────────────


def write_chart_data(days_data: list, yesterday_price: float):
    """将走势图数据写入 JSON 文件，供页面轮询读取。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    data = {
        "days": days_data,
        "yesterday_price": yesterday_price,
    }
    with open(CHART_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


# ── HTTP 服务 ──────────────────────────────────────


def _read_data() -> bytes:
    """读取数据文件并返回 JSON 字节。"""
    if not os.path.exists(CHART_DATA_FILE):
        return b'{"days": [], "yesterday_price": 0}'
    try:
        with open(CHART_DATA_FILE, "r", encoding="utf-8") as f:
            return f.read().encode("utf-8")
    except Exception:
        return b'{"days": [], "yesterday_price": 0}'


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            if self.path in ("/", "/index.html"):
                body = HTML_PAGE.encode("utf-8")
                ctype = "text/html; charset=utf-8"
            elif self.path == "/plotly.min.js":
                with open(_PLOTLY_JS_PATH, "rb") as f:
                    body = f.read()
                ctype = "application/javascript"
            elif self.path == "/data":
                body = _read_data()
                ctype = "application/json; charset=utf-8"
            else:
                self.send_error(404)
                return

            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass  # 静默日志


def launch_chart():
    """启动走势图 HTTP 服务线程并打开浏览器。"""
    global _httpd
    if _httpd is not None:
        return  # 已在运行

    httpd = None
    for port in PORT_RANGE:
        try:
            httpd = HTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    if httpd is None:
        raise RuntimeError("没有可用端口 (8787-8799)")

    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    _httpd = httpd
    webbrowser.open(f"http://127.0.0.1:{httpd.server_address[1]}/")


def kill_chart():
    """停止走势图 HTTP 服务。"""
    global _httpd
    if _httpd is None:
        return
    httpd = _httpd
    _httpd = None
    try:
        httpd.shutdown()
        httpd.server_close()
    except Exception:
        pass


def is_chart_running() -> bool:
    """检查走势图服务是否在运行。"""
    return _httpd is not None
