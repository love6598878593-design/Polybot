#!/usr/bin/env python3
"""
Polymarket 交易机器人 — 学习版 (Railway 部署)
可插拔策略架构 + 真实CLOB签名下单
含 Web Health Check 供 Railway 使用
"""
import json, time, logging, sys, os
from datetime import datetime
from flask import Flask, jsonify
import threading

# ─── 日志 ───
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("pm_bot.log"), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("pm")

# ─── 可调参数 ───
BET = 4.0
MAX_OCCUPIED = 8.0
LOSS_LIMIT = 3.0
CYCLE_S = 300

# ─── Telegram 通知 ───
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "8493341941:AAEshKfSO9jO3wX69EoCJdvIzjlziLinVlk")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "5739995837")

def tg_send(msg):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        _r.post(url, json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        log.warning(f"TG通知失败: {e}")

def tg_notify(msg, level="info"):
    log.info(f"[TG] {msg}")
    tg_send(msg)

# ─── API ───
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
CHAIN = 137
CLOB_CONTRACT = "0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC"

# ─── 网络请求 ───
try:
    import requests as _r
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

def _proxy():
    p = {}
    for k in ['https_proxy','HTTPS_PROXY','http_proxy','HTTP_PROXY','all_proxy','ALL_PROXY']:
        v = os.environ.get(k, "").strip()
        if v: p["http"] = v; p["https"] = v; return p
    return None

def _get(url, timeout=20):
    if _HAS_REQUESTS:
        try: r = _r.get(url, headers={"UA":"pm-bot"}, proxies=_proxy(), timeout=timeout); return r.json() if r.status_code==200 else {}
        except Exception as e: log.warning(f"GET {url[:50]}: {e}"); return {}
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    for k in ['http_proxy','https_proxy']:
        if os.environ.get(k)=="": os.environ.pop(k,None)
    try:
        with urlopen(Request(url,headers={"User-Agent":"pm-bot"}), timeout=timeout) as r:
            return json.loads(r.read().decode())
    except: return {}

def _post(url, data):
    if _HAS_REQUESTS:
        try:
            h = {"Content-Type":"application/json","UA":"pm-bot"}
            r = _r.post(url, json=data, headers=h, proxies=_proxy(), timeout=15)
            return r.json() if r.status_code in [200,201] else {"error":f"HTTP{r.status_code}"}
        except Exception as e: return {"error":str(e)}
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    try:
        r = Request(url, data=json.dumps(data).encode(),
                   headers={"Content-Type":"application/json","User-Agent":"pm-bot"}, method="POST")
        with urlopen(r, timeout=15) as resp: return json.loads(resp.read().decode())
    except HTTPError as e:
        body = e.read().decode()[:300] if e.fp else ""
        return {"error":str(e),"body":body}
    except Exception as e: return {"error":str(e)}

# ═══════════════════════════════════════════════
# 1. 签名引擎
# ═══════════════════════════════════════════════
from eth_account import Account
from eth_account.messages import encode_typed_data

def eip712_order(maker, token_id, price, size, side, expire, salt):
    pr = int(price * 10**6)
    sr = int(size * 10**6)
    if side == "BUY":
        maker_amt = pr * sr // 10**6
        taker_amt = sr
    else:
        maker_amt = sr
        taker_amt = pr * sr // 10**6
    
    return {
        "types": {
            "EIP712Domain": [
                {"name":"name","type":"string"},{"name":"version","type":"string"},
                {"name":"chainId","type":"uint256"},{"name":"verifyingContract","type":"address"},
            ],
            "Order": [
                {"name":"salt","type":"uint256"},{"name":"maker","type":"address"},
                {"name":"signer","type":"address"},{"name":"taker","type":"address"},
                {"name":"tokenId","type":"uint256"},{"name":"makerAmount","type":"uint256"},
                {"name":"takerAmount","type":"uint256"},{"name":"expiration","type":"uint256"},
                {"name":"nonce","type":"uint256"},{"name":"feeRateBps","type":"uint256"},
                {"name":"side","type":"uint8"},{"name":"signatureType","type":"uint256"},
            ],
        },
        "primaryType": "Order",
        "domain": {"name":"Polymarket CLOB","version":"1","chainId":CHAIN,"verifyingContract":CLOB_CONTRACT},
        "message": {
            "salt":salt,"maker":maker,"signer":maker,
            "taker":"0x0000000000000000000000000000000000000000",
            "tokenId":int(token_id),
            "makerAmount":maker_amt,"takerAmount":taker_amt,
            "expiration":expire,"nonce":0,"feeRateBps":0,
            "side":0 if side=="BUY" else 1,"signatureType":0,
        },
    }

def sign_and_place(pk, maker, token_id, side, price, size):
    expire = int(time.time()) + 120
    salt = int(time.time() * 1000000) % (2**32)
    od = eip712_order(maker, token_id, price, size, side, expire, salt)
    
    acct = Account.from_key(pk)
    enc = encode_typed_data(od)
    signed = Account.sign_message(enc, acct.key)
    sig = "0x" + signed.signature.hex()
    
    payload = {
        "order": {
            "salt": od["message"]["salt"], "maker": maker, "signer": maker,
            "taker": "0x0000000000000000000000000000000000000000",
            "tokenId": str(od["message"]["tokenId"]),
            "makerAmount": str(od["message"]["makerAmount"]),
            "takerAmount": str(od["message"]["takerAmount"]),
            "expiration": str(expire), "nonce": "0", "feeRateBps": "0",
            "side": str(od["message"]["side"]), "signatureType": 0,
            "signature": sig,
        },
        "owner": maker,
    }
    
    return _post(f"{CLOB}/order", payload)

# ═══════════════════════════════════════════════
# 2. 市场扫描
# ═══════════════════════════════════════════════

def scan_markets():
    data = _get(f"{GAMMA}/markets?limit=500&closed=false")
    markets = data.get("data", data) if isinstance(data, dict) else data
    if not isinstance(markets, list):
        return []
    
    signals = []
    for m in markets:
        try:
            t = m.get("clobTokenIds", "[]")
            tids = json.loads(t) if isinstance(t, str) else (t or [])
            if len(tids) < 2: continue
            
            yb = _get(f"{CLOB}/book?token_id={tids[0]}")
            nb = _get(f"{CLOB}/book?token_id={tids[1]}")
            if not yb or not nb: continue
            
            ya = yb.get("asks", [])
            na = nb.get("asks", [])
            ybids = yb.get("bids", [])
            nbids = nb.get("bids", [])
            if not ya or not na: continue
            
            buy_y = float(ya[0]["price"])
            buy_n = float(na[0]["price"])
            total = buy_y + buy_n
            arb = 1.0 - total
            
            sell_y = float(ybids[0]["price"]) if ybids else buy_y * 1.01
            sell_n = float(nbids[0]["price"]) if nbids else buy_n * 1.01
            
            signals.append({
                "question": m.get("question", "?")[:50],
                "condition_id": m.get("conditionId", "") or m.get("conditionID", ""),
                "token_yes": tids[0], "token_no": tids[1],
                "buy_yes": buy_y, "buy_no": buy_n,
                "sell_yes": sell_y, "sell_no": sell_n,
                "total_cost": total, "arb": arb,
                "exit_value": sell_y + sell_n,
                "volume": float(m.get("volume", 0) or 0),
                "liquidity": float(m.get("liquidity", 0) or 0),
            })
        except:
            continue
    
    signals.sort(key=lambda s: s["arb"], reverse=True)
    return signals

# ═══════════════════════════════════════════════
# 3. 仓位管理
# ═══════════════════════════════════════════════

class Manager:
    def __init__(self):
        self.file = "pm_state.json"
        self.positions = []
        self.history = []
        self.stats = {"cycles":0, "trades":0, "wins":0, "losses":0, "pnl":0.0}
        self.load()
    
    def load(self):
        try:
            with open(self.file) as f:
                d = json.load(f)
                self.positions = [p for p in d.get("positions",[]) if p.get("status")=="OPEN"]
                self.history = d.get("history",[])
                self.stats = d.get("stats",self.stats)
        except: pass
    
    def save(self):
        with open(self.file,"w") as f:
            json.dump({
                "positions":self.positions,
                "history":self.history[-500:],
                "stats":self.stats,
                "updated":time.time(),
            }, f, indent=2)
    
    def open(self, pos):
        pos["status"] = "OPEN"
        pos["open_time"] = time.time()
        self.positions.append(pos)
        self.save()
    
    def close(self, cid, pnl):
        for p in self.positions[:]:
            if p["condition_id"] == cid:
                p["status"] = "CLOSED"
                p["pnl"] = round(pnl, 4)
                p["close_time"] = time.time()
                p["hold_m"] = round((p["close_time"]-p["open_time"])/60, 1)
                self.history.append(p)
                self.positions.remove(p)
                self.stats["trades"] += 1
                self.stats["pnl"] += pnl
                if pnl >= 0: self.stats["wins"] += 1
                else: self.stats["losses"] += 1
                self.save()
                return True
        return False
    
    def daily_pnl(self):
        return sum(h.get("pnl",0) for h in self.history
                  if h.get("close_time",0) > time.time()-86400)
    
    def occupied(self):
        return sum(p.get("size",0)*2 for p in self.positions)
    
    def summary(self):
        lines = [
            f"📊 #{self.stats['cycles']} | PnL:${self.stats['pnl']:.2f} "
            f"今日:${self.daily_pnl():.2f} "
            f"| {self.stats['wins']}W/{self.stats['losses']}L "
            f"| 开仓:{len(self.positions)} ${self.occupied():.0f}"
        ]
        for p in self.positions:
            lines.append(f"  {p['question'][:35]} arb={p['arb']:.3f} "
                        f"hold={(time.time()-p['open_time'])/60:.0f}m")
        return "\n".join(lines)

# ═══════════════════════════════════════════════
# 4. 主循环
# ═══════════════════════════════════════════════

bot_instance = None

class Bot:
    def __init__(self, pk):
        self.pk = pk
        self.maker = Account.from_key(pk).address
        self.mgr = Manager()
        self._running = False
        global bot_instance
        bot_instance = self
        log.info(f"钱包: {self.maker[:10]}...{self.maker[-6:]}")
        log.info(f"参数: ${BET}/边 ${MAX_OCCUPIED}上限 日亏${LOSS_LIMIT}停止")
        tg_notify(f"🤖 Polymarket Bot 启动\n💰 钱包: {self.maker[:8]}...\n📐 ${BET}/边 | 上限${MAX_OCCUPIED} | 日亏${LOSS_LIMIT}停止")
    
    def run(self):
        self._running = True
        log.info(f"\n{'='*50}")
        log.info("🚀 启动")
        log.info(f"{'='*50}\n")
        try:
            while self._running:
                self._cycle()
                wait = CYCLE_S - (time.time() % CYCLE_S)
                log.info(f"⏳ {wait:.0f}s后下一轮")
                time.sleep(wait)
        except KeyboardInterrupt:
            log.info("🛑 手动停止")
    
    def stop(self):
        self._running = False
    
    def _cycle(self):
        self.mgr.stats["cycles"] += 1
        log.info(f"\n{'─'*40}")
        log.info(f"🔁 第{self.mgr.stats['cycles']}轮 {datetime.now().strftime('%H:%M')}")
        
        if self.mgr.daily_pnl() <= -LOSS_LIMIT:
            log.error(f"🛑 日亏${self.mgr.daily_pnl():.2f} 已达上限")
            tg_notify(f"🛑 <b>风控触发！</b> 日亏${self.mgr.daily_pnl():.2f} 已达${LOSS_LIMIT}上限，停止交易")
            os._exit(0)
        
        self._check_open()
        
        signals = scan_markets()
        arb = [s for s in signals if s["arb"] >= 0.005]
        log.info(f"扫描: {len(signals)}总 {len(arb)}可套利")
        
        if arb:
            taken = 0
            for s in arb:
                cost = BET * 2
                if self.mgr.occupied() + cost > MAX_OCCUPIED:
                    break
                if any(p["condition_id"]==s["condition_id"] for p in self.mgr.positions):
                    continue
                
                log.info(f"📈 {s['question'][:35]}")
                log.info(f"   买Yes@{s['buy_yes']:.4f}+No@{s['buy_no']:.4f} "
                        f"={s['total_cost']:.4f} arb={s['arb']:.3f}")
                
                yr = sign_and_place(self.pk, self.maker, s["token_yes"], "BUY",
                                   s["buy_yes"], BET)
                nr = sign_and_place(self.pk, self.maker, s["token_no"], "BUY",
                                   s["buy_no"], BET)
                
                self.mgr.open({
                    "condition_id": s["condition_id"],
                    "question": s["question"][:40],
                    "token_yes": s["token_yes"], "token_no": s["token_no"],
                    "buy_yes": s["buy_yes"], "buy_no": s["buy_no"],
                    "sell_yes": s["sell_yes"], "sell_no": s["sell_no"],
                    "arb": s["arb"], "size": BET,
                })
                taken += 1
                log.info(f"   ✅ Yes:{yr.get('status','?')} No:{nr.get('status','?')}")
                tg_notify(f"📈 开仓: {s['question'][:25]} arb={s['arb']:.3f} ${BET}/边")
            
            if taken:
                log.info(f"新开{taken}笔")
        
        log.info(self.mgr.summary())
    
    def _check_open(self):
        for p in self.mgr.positions[:]:
            m = _get(f"{GAMMA}/markets/{p['condition_id']}")
            if not m: continue
            try:
                pr = m.get("outcomePrices","[]")
                prices = json.loads(pr) if isinstance(pr,str) else (pr or [])
                if len(prices) < 2: continue
                cur_sum = float(prices[0]) + float(prices[1])
            except: continue
            
            hold = (time.time() - p["open_time"]) / 60
            should = False; reason = ""
            if cur_sum >= 0.995:
                should = True; reason = f"收敛{cur_sum:.4f}"
            elif hold >= 60:
                should = True; reason = f"超时{hold:.0f}m"
            
            if should:
                ys = sign_and_place(self.pk, self.maker, p["token_yes"], "SELL",
                                   p["sell_yes"], BET)
                ns = sign_and_place(self.pk, self.maker, p["token_no"], "SELL",
                                   p["sell_no"], BET)
                
                gross = (1.0 - p["buy_yes"] - p["buy_no"]) * BET * 2
                fees = 0.001 * BET * 2
                pnl = gross - fees
                
                self.mgr.close(p["condition_id"], pnl)
                ico = "✅" if pnl >= 0 else "❌"
                log.info(f"{ico} {p['question'][:30]} ${pnl:+.2f} {reason}")
                tg_notify(f"{ico} 平仓: {p['question'][:25]} ${pnl:+.2f} ({reason})")

# ═══════════════════════════════════════════════
# 5. Flask Web服务 (用于Railway Health Check)
# ═══════════════════════════════════════════════

app = Flask(__name__)

@app.route("/")
def health():
    info = {"status": "running", "uptime": 0}
    if bot_instance:
        info.update({
            "cycles": bot_instance.mgr.stats["cycles"],
            "trades": bot_instance.mgr.stats["trades"],
            "pnl": bot_instance.mgr.stats["pnl"],
            "wins": bot_instance.mgr.stats["wins"],
            "losses": bot_instance.mgr.stats["losses"],
            "open_positions": len(bot_instance.mgr.positions),
            "daily_pnl": bot_instance.mgr.daily_pnl(),
            "wallet": bot_instance.maker[:10] + "...",
        })
    return jsonify(info)

@app.route("/log")
def recent_log():
    try:
        with open("pm_bot.log") as f:
            lines = f.readlines()[-50:]
        return jsonify({"lines": lines})
    except:
        return jsonify({"lines": []})

# ═══════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════

def main():
    pk = os.environ.get("POLY_PRIVATE_KEY", "")
    if not pk and len(sys.argv) > 1:
        pk = sys.argv[1]
    if not pk:
        print("❌ 需要设置 POLY_PRIVATE_KEY 环境变量")
        sys.exit(1)
    if not pk.startswith("0x"):
        pk = "0x" + pk
    
    # 在后台线程中运行bot
    bot = Bot(pk)
    t = threading.Thread(target=bot.run, daemon=True)
    t.start()
    
    # Flask web服务 (Railway需要)
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
