import argparse
import csv
import datetime
import json
import os
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from Phidget22.PhidgetException import PhidgetException

from bridge_common import apply_calibration, load_calibration, open_channel

STATE = {}
LOCK = threading.Lock()
CSV_LOCK = threading.Lock()
CONFIG = {}


def sampler(channel, ch, interval_s, maxlen):
    buf = STATE[channel]["points"]
    unit = STATE[channel]["unit"]
    while not CONFIG["stop"].is_set():
        try:
            ratio = ch.getVoltageRatio()
        except PhidgetException:
            time.sleep(interval_s)
            continue
        value = apply_calibration(CONFIG["cal"], channel, ratio)
        if value is None:
            value = ratio
        t_ms = int(time.time() * 1000)
        with LOCK:
            buf.append((t_ms, value))
            STATE[channel]["ratio"] = ratio
        writer = CONFIG.get("csv_writer")
        if writer is not None:
            iso = datetime.datetime.fromtimestamp(t_ms / 1000.0).isoformat()
            with CSV_LOCK:
                writer.writerow([t_ms, iso, channel, "{:.9f}".format(ratio), value, unit])
                CONFIG["csv_file"].flush()
        time.sleep(interval_s)


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>PhidgetBridge 1046 - Live</title>
<style>
  body{background:#111;color:#ddd;font-family:system-ui,sans-serif;margin:0;padding:16px}
  h1{font-size:16px;font-weight:600;margin:0 0 12px}
  .chart{margin-bottom:18px}
  .lbl{display:flex;justify-content:space-between;font-size:13px;margin-bottom:4px}
  .val{font-variant-numeric:tabular-nums}
  canvas{width:100%;height:220px;background:#181818;border:1px solid #333;border-radius:6px}
</style></head><body>
<h1>PhidgetBridge 1046 &mdash; live (window __WINDOW__s)</h1>
<div id="charts"></div>
<script>
const WINDOW = __WINDOW__ * 1000;
let charts = {};
function ensure(ch){
  if(charts[ch]) return charts[ch];
  const wrap=document.createElement('div'); wrap.className='chart';
  const lbl=document.createElement('div'); lbl.className='lbl';
  const name=document.createElement('span'); name.textContent='Channel '+ch;
  const val=document.createElement('span'); val.className='val';
  lbl.appendChild(name); lbl.appendChild(val);
  const cv=document.createElement('canvas');
  wrap.appendChild(lbl); wrap.appendChild(cv);
  document.getElementById('charts').appendChild(wrap);
  charts[ch]={canvas:cv,val:val};
  return charts[ch];
}
function draw(ch, series, now){
  const c=ensure(ch); const cv=c.canvas;
  const dpr=window.devicePixelRatio||1;
  const w=cv.clientWidth, h=cv.clientHeight;
  cv.width=w*dpr; cv.height=h*dpr;
  const g=cv.getContext('2d'); g.scale(dpr,dpr);
  g.clearRect(0,0,w,h);
  const pts=series.points; const unit=series.unit; const cap=series.cap;
  if(pts.length<2){return;}
  let mn=Infinity,mx=-Infinity;
  for(const p of pts){ if(p[1]<mn)mn=p[1]; if(p[1]>mx)mx=p[1]; }
  if(mn===mx){mn-=1e-6;mx+=1e-6;}
  const pad=(mx-mn)*0.1||1e-6; mn-=pad; mx+=pad;
  const t0=now-WINDOW, t1=now;
  const x=t=>((t-t0)/(t1-t0))*(w-60)+50;
  const y=v=>h-20-((v-mn)/(mx-mn))*(h-40);
  g.strokeStyle='#333'; g.fillStyle='#888'; g.font='11px sans-serif'; g.lineWidth=1;
  for(let i=0;i<=4;i++){
    const yy=20+i*(h-40)/4; const vv=mx-(i*(mx-mn)/4);
    g.beginPath(); g.moveTo(50,yy); g.lineTo(w-10,yy); g.stroke();
    g.fillText(vv.toPrecision(4),4,yy+3);
  }
  if(cap){
    const limits=[[cap*0.9,'#e6a23c'],[cap,'#e05555'],[-cap*0.9,'#e6a23c'],[-cap,'#e05555']];
    g.setLineDash([5,4]); g.lineWidth=1;
    for(const [lv,col] of limits){
      if(lv>=mn&&lv<=mx){
        const ly=y(lv); g.strokeStyle=col;
        g.beginPath(); g.moveTo(50,ly); g.lineTo(w-10,ly); g.stroke();
      }
    }
    g.setLineDash([]);
  }
  g.strokeStyle='#4ea1ff'; g.lineWidth=1.5; g.beginPath();
  let started=false;
  for(const p of pts){
    const px=x(p[0]), py=y(p[1]);
    if(!started){g.moveTo(px,py);started=true;} else {g.lineTo(px,py);}
  }
  g.stroke();
  const last=pts[pts.length-1][1];
  let txt=last.toFixed(unit==='V/V'?9:3)+' '+unit;
  let col='#7ee787';
  if(cap){
    const frac=Math.abs(last)/cap;
    txt+='  ('+(frac*100).toFixed(0)+'% of '+cap+unit+')';
    if(frac>=1.0){col='#e05555'; txt+='  OVERLOAD';}
    else if(frac>=0.9){col='#e6a23c'; txt+='  WARN';}
  }else{col='#ddd';}
  c.val.style.color=col; c.val.textContent=txt;
}
async function tick(){
  try{
    const r=await fetch('/data'); const d=await r.json();
    for(const ch of Object.keys(d.series)){ draw(ch,d.series[ch],d.now); }
  }catch(e){}
}
setInterval(tick,200); tick();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/data"):
            now = int(time.time() * 1000)
            series = {}
            with LOCK:
                for ch, st in STATE.items():
                    series[str(ch)] = {
                        "unit": st["unit"],
                        "cap": st["cap"],
                        "points": list(st["points"]),
                    }
            body = json.dumps({"now": now, "series": series}).encode()
            self._send(200, body, "application/json")
        elif self.path == "/" or self.path.startswith("/index"):
            html = PAGE.replace("__WINDOW__", str(CONFIG["window"]))
            self._send(200, html.encode(), "text/html; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")


def main():
    parser = argparse.ArgumentParser(
        description="Live web plot for a PhidgetBridge 1046."
    )
    parser.add_argument("--channels", type=int, nargs="+", default=[0, 3])
    parser.add_argument("--gain", type=int, default=128, choices=[1, 2, 4, 8, 16, 32, 64, 128])
    parser.add_argument("--interval", type=int, default=100, help="Sample interval ms. Default: 100")
    parser.add_argument("--window", type=int, default=30, help="Plot window seconds. Default: 30")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--csv", nargs="?", const="__auto__", default=None,
        help="Log samples to CSV. Give a path, or pass --csv alone for an auto-named file.",
    )
    args = parser.parse_args()

    CONFIG["window"] = args.window
    CONFIG["stop"] = threading.Event()
    CONFIG["cal"] = load_calibration()

    if args.csv is not None:
        path = args.csv
        if path == "__auto__":
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "strain_log_{}.csv".format(stamp),
            )
        csv_file = open(path, "w", newline="")
        writer = csv.writer(csv_file)
        writer.writerow(["epoch_ms", "iso", "channel", "ratio", "value", "unit"])
        csv_file.flush()
        CONFIG["csv_file"] = csv_file
        CONFIG["csv_writer"] = writer
        CONFIG["csv_path"] = path

    interval_s = args.interval / 1000.0
    maxlen = int(args.window * 1000 / max(args.interval, 1)) + 10

    opened = []
    threads = []
    try:
        for c in args.channels:
            ch, _ = open_channel(c, gain=args.gain, interval_ms=args.interval)
            unit = "V/V"
            cap = None
            entry = CONFIG["cal"].get(str(c))
            if entry is not None:
                unit = entry["unit"]
                cap = entry.get("capacity")
            STATE[c] = {"points": deque(maxlen=maxlen), "unit": unit, "ratio": 0.0, "cap": cap}
            opened.append(ch)
            t = threading.Thread(target=sampler, args=(c, ch, interval_s, maxlen), daemon=True)
            t.start()
            threads.append(t)
    except PhidgetException as exc:
        print("Phidget error (code {}): {}".format(exc.code, exc.details), file=sys.stderr)
        sys.exit(1)

    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print("Live plot serving on:")
    print("  http://localhost:{}".format(args.port))
    print("  http://<this-pi-ip>:{}   (from another device)".format(args.port))
    print("Channels: {}  gain: {}  (Ctrl+C to stop)".format(args.channels, args.gain))
    if "csv_path" in CONFIG:
        print("Logging to: {}".format(CONFIG["csv_path"]))
    try:
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        print("\nStopping...")
    finally:
        CONFIG["stop"].set()
        server.shutdown()
        for ch in opened:
            ch.close()
        if "csv_file" in CONFIG:
            CONFIG["csv_file"].close()
            print("Saved log: {}".format(CONFIG["csv_path"]))


if __name__ == "__main__":
    main()
