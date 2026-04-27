#!/usr/bin/env python3
from flask import Flask, jsonify
import json, time, logging, os, threading
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("pm")

BET = 4.0
MAX_OCCUPIED = 20.0
CYCLE_S = 300
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
CHAIN = 137
CLOB_CONTRACT = "0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC"
TARGET_SLUGS = {"btc-5m":"BTC","eth-5m":"ETH","sol-5m":"SOL","xrp-5m":"XRP","doge-5m":"DOGE","hype-5m":"HYPE","bnb-5m":"BNB"}

import requests as _r
def _get(url, to=15):
    try:
        r = _r.get(url, headers={"User-Agent":"pm-bot"}, timeout=to)
        return r.json() if r.status_code==200 else {}
    except:
        return {}

def _post(url, d):
    try:
        r = _r.post(url, json=d, headers={"Content-Type":"application/json","User-Agent":"pm-bot"}, timeout=15)
        return r.json() if r.status_code in [200,201] else {"error":f"HTTP{r.status_code}"}
    except Exception as e:
        return {"error":str(e)}

from eth_account import Account
from eth_account.messages import encode_typed_data

def eip712(maker, tid, price, size, side, expire, salt):
    pr = int(price * 1e6)
    sr = int(size * 1e6)
    if side == "BUY":
        ma = pr * sr // 10**6; ta = sr
    else:
        ma = sr; ta = pr * sr // 10**6
    return {
        "types": {
            "EIP712Domain": [{"name":"name","type":"string"},{"name":"version","type":"string"},{"name":"chainId","type":"uint256"},{"name":"verifyingContract","type":"address"}],
            "Order": [{"name":"salt","type":"uint256"},{"name":"maker","type":"address"},{"name":"signer","type":"address"},{"name":"taker","type":"address"},{"name":"tokenId","type":"uint256"},{"name":"makerAmount","type":"uint256"},{"name":"takerAmount","type":"uint256"},{"name":"expiration","type":"uint256"},{"name":"nonce","type":"uint256"},{"name":"feeRateBps","type":"uint256"},{"name":"side","type":"uint8"},{"name":"signatureType","type":"uint256"}]
        },
        "primaryType": "Order",
        "domain": {"name":"Polymarket CLOB","version":"1","chainId":CHAIN,"verifyingContract":CLOB_CONTRACT},
        "message": {"salt":salt,"maker":maker,"signer":maker,"taker":"0x0000000000000000000000000000000000000000","tokenId":int(tid),"makerAmount":ma,"takerAmount":ta,"expiration":expire,"nonce":0,"feeRateBps":0,"side":0 if side=="BUY" else 1,"signatureType":0}
    }

def sign_order(pk, maker, tid, side, price, size):
    expire = int(time.time()) + 120
    salt = int(time.time() * 1000000) % (2**32)
    msg = eip712(maker, tid, price, size, side, expire, salt)
    acct = Account.from_key(pk)
    try:
        signed = acct.sign_typed_data(msg)
        sig = "0x" + signed.signature.hex()
    except:
        enc = encode_typed_data(msg)
        signed = Account.sign_message(enc, acct.key)
        sig = "0x" + signed.signature.hex()
    return _post(f"{CLOB}/order", {
        "order": {"salt":str(msg["message"]["salt"]),"maker":maker,"signer":maker,"taker":"0x0000000000000000000000000000000000000000","tokenId":str(int(tid)),"makerAmount":str(msg["message"]["makerAmount"]),"takerAmount":str(msg["message"]["takerAmount"]),"expiration":str(expire),"nonce":"0","feeRateBps":"0","side":str(msg["message"]["side"]),"signatureType":0,"signature":sig},
        "owner": maker
    })

price_history = {}

def scan():
    data = _get(f"{GAMMA}/markets?limit=500&closed=false&tag=5-minute")
    markets = data if isinstance(data, list) else data.get("data", data) if isinstance(data, dict) else []
    if not isinstance(markets, list): markets = []
    res = {}
    for m in markets:
        try:
            slug = m.get("slug","").lower()
            if slug not in TARGET_SLUGS: continue
            name = TARGET_SLUGS[slug]
            cid = m.get("conditionId","")
            tids_raw = m.get("clobTokenIds","[]")
            tids = json.loads(tids_raw) if isinstance(tids_raw,str) else (tids_raw or [])
            if len(tids) < 2 or not cid: continue
            yb = _get(f"{CLOB}/book?token_id={tids[0]}")
            nb = _get(f"{CLOB}/book?token_id={tids[1]}")
            if not yb or not nb: continue
            ya = yb.get("asks",[]); ybids = yb.get("bids",[])
            na = nb.get("asks",[]); nbids = nb.get("bids",[])
            if not ya or not na: continue
            yes_a = float(ya[0]["price"]); yes_b = float(ybids[0]["price"]) if ybids else yes_a * 0.98
            no_a = float(na[0]["price"]); no_b = float(nbids[0]["price"]) if nbids else no_a * 0.98
            yes_m = (yes_b + yes_a) / 2; no_m = (no_b + no_a) / 2
            prev = price_history.get(slug, {})
            yt = "up" if prev.get("yes",0) < yes_m else ("down" if prev.get("yes",0) > yes_m else "flat")
            nt = "up" if prev.get("no",0) < no_m else ("down" if prev.get("no",0) > no_m else "flat")
            price_history[slug] = {"yes": yes_m, "no": no_m}
            cheap = "YES" if yes_m <= no_m else "NO"
            cheap_p = min(yes_m, no_m)
            diff = abs(no_m - yes_m)
            ts = None; tst = 0
            if yt == "up": ts="YES"; tst=yes_m - prev.get("yes",yes_m)
            if nt == "up": ts="NO"; tst=no_m - prev.get("no",no_m)
            res[slug] = {"name":name,"condition_id":cid,"token_yes":tids[0],"token_no":tids[1], "yes_mid":round(yes_m,4),"no_mid":round(no_m,4),"yes_trend":yt,"no_trend":nt, "cheap_side":cheap,"cheap_price":round(cheap_p,4),"price_diff":round(diff,4), "trend_side":ts,"trend_strength":round(tst,4) if ts else 0,"is_extreme":cheap_p<0.15}
        except:
            continue
    return res

app = Flask(__name__)
bot_pk = os.environ.get("POLY_PRIVATE_KEY","")
bot_maker = ""
bot_pnl=0.0; bot_trades=0; bot_wins=0; bot_losses=0; bot_cycle=0
bot_positions=[]; bot_history=[]; last_scan=""; last_trade=""; last_scan_data={}
if bot_pk: bot_maker = Account.from_key(bot_pk).address

def trade_loop():
    global bot_pnl,bot_trades,bot_wins,bot_losses,bot_cycle,bot_positions,bot_history,last_scan,last_trade,last_scan_data
    while True:
        try:
            bot_cycle += 1
            now = datetime.now()
            minute = now.minute % 5
            sec = now.second
            log.info(f"\n{'='*55}")
            log.info(f"v4 第{bot_cycle}轮 {now.strftime('%H:%M:%S')} {minute}m{sec}s 开仓{len(bot_positions)}笔 PnL:${bot_pnl:.2f}")

            # 平仓
            for p in bot_positions[:]:
                hid = (time.time()-p["ot"])/60
                tid = p["tid"]; bp = p["bp"]; sz = p["sz"]; side = p["side"]
                book = _get(f"{CLOB}/book?token_id={tid}")
                if not book: continue
                bids = book.get("bids",[])
                cur = float(bids[0]["price"]) if bids else None
                if not cur: continue
                pnl = cur - bp; sell=False; sp=1.0; reason=""
                if cur >= 0.95: sell=True; reason=f"近1.0@{cur:.4f}"
                elif 1.0<=hid<=3.0 and not p.get("ps",False): sell=True; sp=0.75; p["ps"]=True; reason=f"中段{hid:.1f}m"
                elif hid>=4.0: sell=True; reason=f"超时{hid:.1f}m"
                elif pnl>0.02 and hid>=1.5: sell=True; reason=f"盈利${pnl:.4f}"
                if sell:
                    ss = sz * sp
                    if ss < 0.01: continue
                    r = sign_order(bot_pk,bot_maker,tid,"SELL",cur,ss)
                    n = (cur-bp)*ss - 0.001*ss
                    bot_pnl += n; bot_trades += 1
                    if n>=0: bot_wins+=1
                    else: bot_losses+=1
                    bot_history.append({"t":time.time(),"name":p["name"],"side":side,"buy":bp,"sell":cur,"sz":ss,"pnl":round(n,4),"reason":reason})
                    rem = sz - ss
                    if rem<0.01: bot_positions.remove(p); log.info(f"✅ 平 {p['name']} {side} PnL:${n:+.4f}")
                    else: p["sz"]=rem; log.info(f"⏳ {p['name']} 剩{rem:.2f}等结算")

            # 扫描
            markets = scan()
            last_scan_data = markets

            # 开仓决策
            opps = []
            for slug,m in markets.items():
                name = m["name"]
                if minute>=4 and sec>30: continue
                cheap = m["cheap_side"]; cheap_p = m["cheap_price"]; diff = m["price_diff"]
                if diff>=0.02 or m["is_extreme"]:
                    sz = BET
                    if m["is_extreme"]: sz = BET*0.5
                    opps.append((slug,name,m["condition_id"],m["token_yes"] if cheap=="YES" else m["token_no"],cheap,cheap_p,sz))
                    log.info(f"  {name}: 买{cheap}@{cheap_p:.4f} diff={diff:.4f}")
                ts = m["trend_side"]; tst = m["trend_strength"]
                if ts and diff<0.10 and tst>0.01:
                    tp = m["yes_mid"] if ts=="YES" else m["no_mid"]
                    opps.append((slug,name,m["condition_id"],m["token_yes"] if ts=="YES" else m["token_no"],ts,tp,BET*0.3))
                    log.info(f"  {name}: 追{ts}@{tp:.4f}")

            for slug,name,cid,tid,side,price,sz in opps:
                occ = sum(p.get("sz",0)*2 for p in bot_positions)
                if occ+BET*2 > MAX_OCCUPIED: break
                if any(p["cid"]==cid for p in bot_positions): continue
                r = sign_order(bot_pk,bot_maker,tid,"BUY",price,sz)
                log.info(f"📈 开{name} 买{side}@{price:.4f}x{sz:.1f} -> {r.get('status','?') if isinstance(r,dict) else '?'}")
                bot_positions.append({"cid":cid,"name":name,"side":side,"tid":tid,"bp":price,"sz":sz,"ot":time.time()})
                last_trade = f"{now.strftime('%H:%M')} {name} {side}@{price:.4f}"

            occ = sum(p.get("sz",0)*2 for p in bot_positions)
            log.info(f"📊 PnL:${bot_pnl:.2f} 开仓{len(bot_positions)}笔 ${occ:.1f} 历史{bot_trades}笔 {bot_wins}W/{bot_losses}L")
            last_scan = f"{now.strftime('%H:%M')} {len(markets)}个市场"

        except Exception as e:
            log.error(f"异常:{e}")
            import traceback; log.error(traceback.format_exc())
        time.sleep(CYCLE_S - (time.time() % CYCLE_S))

@app.route("/")
def home():
    mr = ""
    for s in ["btc-5m","eth-5m","sol-5m","xrp-5m","doge-5m","hype-5m","bnb-5m"]:
        m = last_scan_data.get(s,{})
        if m:
            mr += f"<tr><td>{m['name']}</td><td>{m['yes_mid']:.4f}</td><td>{m['no_mid']:.4f}</td><td>{m['cheap_side']}@{m['cheap_price']:.4f}</td><td>{m['price_diff']:.4f}</td></tr>"
        else:
            mr += f"<tr><td>{s[:3].upper()}</td><td colspan=4 style='color:#666'>无数据</td></tr>"
    ph = ""
    for p in bot_positions:
        h = (time.time()-p["ot"])/60
        ph += f"<div>{p['name']} {p['side']} @${p['bp']:.4f}x{p['sz']:.1f} hold{h:.0f}m</div>"
    rh = bot_history[-8:] if bot_history else []
    hh = "<br>".join(f"{'🟢' if h['pnl']>=0 else '🔴'}{h['name']} ${h['pnl']:+.2f}({h['reason']})" for h in reversed(rh)) if rh else "<div>暂无</div>"
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content="15"><style>
body{{font:14px monospace;background:#0a0a1a;color:#eee;padding:20px;max-width:750px;margin:auto}}
h1{{color:#00d2ff}} .card{{background:#1a1a2e;border-radius:8px;padding:15px;margin:8px 0}}
.pnl{{font-size:28px;font-weight:bold;color:{'#0f8' if bot_pnl>=0 else '#f44'}}}
.gray{{color:#888}} table{{width:100%}} th{{color:#888;text-align:left}} td{{padding:3px;border-bottom:1px solid #222}}
</style></head><body>
<h1>PM Bot v4</h1>
<div class=card><div class=pnl>${bot_pnl:.2f}</div><div>{bot_trades}笔 {bot_wins}W/{bot_losses}L {round(bot_wins/bot_trades*100,1) if bot_trades else 0}% | {bot_cycle}轮 | 开仓{len(bot_positions)}</div></div>
<div class=card><h2>7币种</h2><table><tr><th>币</th><th>Yes</th><th>No</th><th>便宜边</th><th>价差</th></tr>{mr}</table></div>
<div class=card><h2>开仓</h2>{ph if ph else '<div class=gray>无</div>'}</div>
<div class=card><h2>最近交易</h2>{hh}</div>
</body></html>"""

@app.route("/api/status")
def st():
    return jsonify({"pnl":round(bot_pnl,2),"trades":bot_trades,"wins":bot_wins,"losses":bot_losses,"cycle":bot_cycle,"positions":len(bot_positions)})

@app.route("/api/history")
def h():
    return jsonify(bot_history[-50:])

if __name__ == "__main__":
    if not bot_pk:
        log.error("请设置 POLY_PRIVATE_KEY")
        exit(1)
    log.info(f"钱包:{bot_maker[:10]}.. | $4/边 | 上限$20 | 监控{len(TARGET_SLUGS)}币种")
    threading.Thread(target=trade_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
