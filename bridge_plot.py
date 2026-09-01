import argparse
import csv
import datetime
import json
import os
import struct
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
MB_LOCK = threading.Lock()
CONFIG = {}

HR_CH_BASE = 10
HR_CH_STRIDE = 10
HR_TARGET = 101
HR_ACTIVE_CH = 103
HR_STATE = 110
HR_FINAL = 111
COIL_START = 200
COIL_ABORT = 201

FILL_NAMES = {0: "Idle", 1: "Coarse", 2: "Dribble", 3: "Done", 4: "Fault"}


def regs_to_f32(hi, lo):
    return struct.unpack(">f", struct.pack(">HH", hi & 0xFFFF, lo & 0xFFFF))[0]


def f32_to_regs(value):
    b = struct.pack(">f", float(value))
    return [(b[0] << 8) | b[1], (b[2] << 8) | b[3]]


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


def mb_read_regs(client, address, count):
    with MB_LOCK:
        rr = client.read_holding_registers(address, count, slave=CONFIG["unit_id"])
    if rr is None or rr.isError():
        return None
    return rr.registers


def mb_write_regs(client, address, values):
    with MB_LOCK:
        wr = client.write_registers(address, values, slave=CONFIG["unit_id"])
    return wr is not None and not wr.isError()


def mb_write_coil(client, address, value):
    with MB_LOCK:
        wr = client.write_coil(address, bool(value), slave=CONFIG["unit_id"])
    return wr is not None and not wr.isError()


def modbus_poller(channel, base, interval_s):
    client = CONFIG["mb_client"]
    buf = STATE[channel]["points"]
    unit = STATE[channel]["unit"]
    while not CONFIG["stop"].is_set():
        regs = mb_read_regs(client, base, 8)
        if regs is None:
            time.sleep(interval_s)
            continue
        value = regs_to_f32(regs[2], regs[3])
        t_ms = int(time.time() * 1000)
        with LOCK:
            buf.append((t_ms, value))
            STATE[channel]["ratio"] = value
        writer = CONFIG.get("csv_writer")
        if writer is not None:
            iso = datetime.datetime.fromtimestamp(t_ms / 1000.0).isoformat()
            with CSV_LOCK:
                writer.writerow([t_ms, iso, channel, "", value, unit])
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
  #hmi{background:#181818;border:1px solid #333;border-radius:6px;padding:12px;margin-bottom:18px}
  #hmi h2{font-size:14px;font-weight:600;margin:0 0 10px}
  #hmi .row{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:8px}
  #hmi label{font-size:13px}
  #hmi input{width:110px;background:#111;color:#ddd;border:1px solid #444;border-radius:4px;padding:5px 7px;font-size:13px}
  #hmi button{background:#2a2a2a;color:#ddd;border:1px solid #555;border-radius:4px;padding:6px 14px;font-size:13px;cursor:pointer}
  #hmi button.start{border-color:#2e7d32;color:#7ee787}
  #hmi button.abort{border-color:#a13030;color:#e05555}
  #hmi button:active{background:#3a3a3a}
  #hmi .readout{font-size:13px;font-variant-numeric:tabular-nums}
  #hmi .state{font-weight:600}
  #hmi .lamps{margin-left:auto;display:flex;gap:16px}
  #hmi .lamp{display:inline-flex;align-items:center;gap:6px;font-size:12px}
  #hmi .lamp .dot{width:14px;height:14px;border-radius:50%;background:#2a2a2a;border:1px solid #555}
  #hmi .lamp.coarse .dot.on{background:#4ea1ff;border-color:#4ea1ff;box-shadow:0 0 8px #4ea1ff}
  #hmi .lamp.dribble .dot.on{background:#e6a23c;border-color:#e6a23c;box-shadow:0 0 8px #e6a23c}
</style></head><body>
<h1>PhidgetBridge 1046 &mdash; live (window __WINDOW__s)</h1>
__HMI__
<div id="charts"></div>
<script>
const WINDOW = __WINDOW__ * 1000;
const WARN = __WARN__;
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
    const limits=[[cap*WARN,'#e6a23c'],[cap,'#e05555'],[-cap*WARN,'#e6a23c'],[-cap,'#e05555']];
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
    else if(frac>=WARN){col='#e6a23c'; txt+='  WARN';}
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

async function sendCmd(action, value){
  try{
    const body=JSON.stringify({action:action, value:value});
    await fetch('/cmd',{method:'POST',headers:{'Content-Type':'application/json'},body:body});
  }catch(e){}
}
function initHmi(){
  const panel=document.getElementById('hmi');
  if(!panel) return;
  document.getElementById('h_set').onclick=()=>{
    const v=parseFloat(document.getElementById('h_target').value);
    if(!isNaN(v)) sendCmd('target', v);
  };
  document.getElementById('h_start').onclick=()=>sendCmd('start',0);
  document.getElementById('h_abort').onclick=()=>sendCmd('abort',0);
  async function poll(){
    try{
      const r=await fetch('/fill'); const d=await r.json();
      const st=document.getElementById('h_state');
      st.textContent=d.state_name;
      const cols={0:'#ddd',1:'#4ea1ff',2:'#e6a23c',3:'#7ee787',4:'#e05555'};
      st.style.color=cols[d.state]||'#ddd';
      document.getElementById('h_weight').textContent=d.weight.toFixed(1)+' '+d.unit;
      document.getElementById('h_tgt').textContent=d.target.toFixed(1)+' '+d.unit;
      document.getElementById('h_final').textContent=d.final.toFixed(1)+' '+d.unit;
      document.getElementById('lamp_coarse').classList.toggle('on', d.state===1);
      document.getElementById('lamp_dribble').classList.toggle('on', d.state===2);
    }catch(e){}
  }
  setInterval(poll,300); poll();
}
initHmi();
</script></body></html>"""

HMI_HTML = """<div id="hmi">
<h2>Fill control &mdash; Channel __HMICH__ (__HMIUNIT__)</h2>
<div class="row">
  <label for="h_target">Target</label>
  <input id="h_target" type="number" step="any" value="0">
  <span>__HMIUNIT__</span>
  <button id="h_set">Set</button>
  <button id="h_start" class="start">START</button>
  <button id="h_abort" class="abort">ABORT</button>
  <span class="lamps">
    <span class="lamp coarse"><span class="dot" id="lamp_coarse"></span>Coarse</span>
    <span class="lamp dribble"><span class="dot" id="lamp_dribble"></span>Dribble</span>
  </span>
</div>
<div class="row readout">
  <span>State: <span id="h_state" class="state">-</span></span>
  <span>&nbsp;|&nbsp; Weight: <span id="h_weight">-</span></span>
  <span>&nbsp;|&nbsp; Target: <span id="h_tgt">-</span></span>
  <span>&nbsp;|&nbsp; Final: <span id="h_final">-</span></span>
</div>
</div>"""


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
        elif self.path.startswith("/fill"):
            self._send(200, fill_status().encode(), "application/json")
        elif self.path == "/" or self.path.startswith("/index"):
            html = PAGE.replace("__WINDOW__", str(CONFIG["window"]))
            html = html.replace("__WARN__", repr(CONFIG["warn_frac"]))
            if CONFIG.get("source") == "modbus":
                hmi = HMI_HTML.replace("__HMICH__", str(CONFIG["hmi_channel"]))
                hmi = hmi.replace("__HMIUNIT__", CONFIG.get("hmi_unit", ""))
            else:
                hmi = ""
            html = html.replace("__HMI__", hmi)
            self._send(200, html.encode(), "text/html; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if not self.path.startswith("/cmd"):
            self._send(404, b"not found", "text/plain")
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode() or "{}")
        except ValueError:
            data = {}
        ok = handle_cmd(data.get("action"), data.get("value"))
        body = json.dumps({"ok": bool(ok)}).encode()
        self._send(200 if ok else 400, body, "application/json")


def fill_status():
    client = CONFIG.get("mb_client")
    unit = CONFIG.get("hmi_unit", "")
    out = {"state": 0, "state_name": "n/a", "weight": 0.0, "target": 0.0,
           "final": 0.0, "unit": unit}
    if client is None:
        return json.dumps(out)
    base = CONFIG["hmi_base"]
    regs = mb_read_regs(client, base + 2, 2)
    if regs is not None:
        out["weight"] = round(regs_to_f32(regs[0], regs[1]), 3)
    tgt = mb_read_regs(client, HR_TARGET, 2)
    if tgt is not None:
        out["target"] = round(regs_to_f32(tgt[0], tgt[1]), 3)
    stt = mb_read_regs(client, HR_STATE, 3)
    if stt is not None:
        out["state"] = stt[0]
        out["state_name"] = FILL_NAMES.get(stt[0], str(stt[0]))
        out["final"] = round(regs_to_f32(stt[1], stt[2]), 3)
    return json.dumps(out)


def handle_cmd(action, value):
    client = CONFIG.get("mb_client")
    if client is None:
        return False
    if action == "target":
        try:
            v = float(value)
        except (TypeError, ValueError):
            return False
        ok = mb_write_regs(client, HR_TARGET, f32_to_regs(v))
        ok = mb_write_regs(client, HR_ACTIVE_CH, [CONFIG["hmi_index"]]) and ok
        return ok
    if action == "start":
        return mb_write_coil(client, COIL_START, True)
    if action == "abort":
        return mb_write_coil(client, COIL_ABORT, True)
    return False


def main():
    parser = argparse.ArgumentParser(
        description="Live web plot for a PhidgetBridge 1046."
    )
    parser.add_argument("--channels", type=int, nargs="+", default=[0, 3])
    parser.add_argument("--gain", type=int, default=128, choices=[1, 2, 4, 8, 16, 32, 64, 128])
    parser.add_argument("--interval", type=int, default=100, help="Sample interval ms. Default: 100")
    parser.add_argument("--window", type=int, default=30, help="Plot window seconds. Default: 30")
    parser.add_argument(
        "--warn-frac", type=float, default=0.9, dest="warn_frac",
        help="Fraction of capacity at which the warning triggers (0-1). Default: 0.9",
    )
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--csv", nargs="?", const="__auto__", default=None,
        help="Log samples to CSV. Give a path, or pass --csv alone for an auto-named file.",
    )
    parser.add_argument(
        "--source", choices=["phidget", "modbus"], default="phidget",
        help="phidget = open the 1046 directly. modbus = read the running "
             "bridge_modbus.py gateway and show the fill HMI. Default: phidget",
    )
    parser.add_argument("--modbus-host", default="127.0.0.1", dest="modbus_host")
    parser.add_argument("--modbus-port", type=int, default=1502, dest="modbus_port")
    parser.add_argument("--unit-id", type=int, default=1, dest="unit_id")
    parser.add_argument(
        "--hmi-channel", type=int, default=0, dest="hmi_channel",
        help="Phidget channel the fill HMI drives (modbus source). Default: 0",
    )
    args = parser.parse_args()

    CONFIG["window"] = args.window
    CONFIG["warn_frac"] = args.warn_frac
    CONFIG["stop"] = threading.Event()
    CONFIG["cal"] = load_calibration()
    CONFIG["source"] = args.source
    CONFIG["unit_id"] = args.unit_id

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
    if args.source == "modbus":
        from pymodbus.client import ModbusTcpClient

        client = ModbusTcpClient(args.modbus_host, port=args.modbus_port)
        if not client.connect():
            print("Could not connect to Modbus gateway at {}:{}. "
                  "Is bridge_modbus.py running?".format(args.modbus_host, args.modbus_port),
                  file=sys.stderr)
            sys.exit(1)
        CONFIG["mb_client"] = client

        for i, c in enumerate(args.channels):
            unit = "g"
            cap = None
            entry = CONFIG["cal"].get(str(c))
            if entry is not None:
                unit = entry["unit"]
                cap = entry.get("capacity")
            base = HR_CH_BASE + i * HR_CH_STRIDE
            STATE[c] = {"points": deque(maxlen=maxlen), "unit": unit, "ratio": 0.0, "cap": cap}
            t = threading.Thread(target=modbus_poller, args=(c, base, interval_s), daemon=True)
            t.start()
            threads.append(t)

        hmi_ch = args.hmi_channel
        if hmi_ch not in args.channels:
            hmi_ch = args.channels[0]
        idx = args.channels.index(hmi_ch)
        CONFIG["hmi_channel"] = hmi_ch
        CONFIG["hmi_index"] = idx
        CONFIG["hmi_base"] = HR_CH_BASE + idx * HR_CH_STRIDE
        CONFIG["hmi_unit"] = STATE[hmi_ch]["unit"]
    else:
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
    if args.source == "modbus":
        print("Source: modbus gateway {}:{}  channels {}  HMI -> CH{}".format(
            args.modbus_host, args.modbus_port, args.channels, CONFIG["hmi_channel"]))
    else:
        print("Source: phidget  channels {}  gain {}".format(args.channels, args.gain))
    print("(Ctrl+C to stop)")
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
        if CONFIG.get("mb_client") is not None:
            CONFIG["mb_client"].close()
        if "csv_file" in CONFIG:
            CONFIG["csv_file"].close()
            print("Saved log: {}".format(CONFIG["csv_path"]))


if __name__ == "__main__":
    main()
