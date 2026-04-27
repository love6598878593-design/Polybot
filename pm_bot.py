python
#!/usr/bin/env python3
from flask import Flask, jsonify
import json,time,logging,os,threading
from datetime import datetime
logging.basicConfig(level=logging.INFO,format="%(asctime)s [%(levelname)s] %(message)s")
log=logging.getLogger("pm")
BET=4.0;MAX_OCCUPIED=20.0;CYCLE_S=300
GAMMA="https://gamma-api.polymarket.com";CLOB="https://clob.polymarket.com";CHAIN=137
CLOB_CONTRACT="0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC"
TARGET_SLUGS={"btc-5m":"BTC","eth-5m":"ETH","sol-5m":"SOL","xrp-5m":"XRP","doge-5m":"DOGE","hype-5m":"HYPE","bnb-5m":"BNB"}
import requests as _r
def _get(url,to=15):
 try:r=_r.get(url,headers={"User-Agent":"pm-bot"},timeout=to);return r.json()if r.status_code==200 else{}
 except:return{}
def _post(url,d):
 try:r=_r.post(url,json=d,headers={"Content-Type":"application/json","User-Agent":"pm-bot"},timeout=15);return r.json()if r.status_code in[200,201]else{"error":f"HTTP{r.status_code}"}
 except Exception as e:return{"error":str(e)}
from web3 import Web3
from eth_account import Account
def eip712(maker,tid,price,size,side,expire,salt):
 pr=int(price*1e6);sr=int(size*1e6)
 if side=="BUY":ma=pr*sr//10**6;ta=sr
 else:ma=sr;ta=pr*sr//10**6
 return{"types":{"EIP712Domain":[{"name":"name","type":"string"},{"name":"version","type":"string"},{"name":"chainId","type":"uint256"},{"name":"verifyingContract","type":"address"}],"Order":[{"name":"salt","type":"uint256"},{"name":"maker","type":"address"},{"name":"signer","type":"address"},{"name":"taker","type":"address"},{"name":"tokenId","type":"uint256"},{"name":"makerAmount","type":"uint256"},{"name":"takerAmount","type":"uint256"},{"name":"expiration","type":"uint256"},{"name":"nonce","type":"uint256"},{"name":"feeRateBps","type":"uint256"},{"name":"side","type":"uint8"},{"name":"signatureType","type":"uint256"}]},"primaryType":"Order","domain":{"name":"Polymarket CLOB","version":"1","chainId":CHAIN,"verifyingContract":CLOB_CONTRACT},"message":{"salt":salt,"maker":maker,"signer":maker,"taker":"0x0000000000000000000000000000000000000000","tokenId":int(tid),"makerAmount":ma,"takerAmount":ta,"expiration":expire,"nonce":0,"feeRateBps":0,"side":0 if side=="BUY" else 1,"signatureType":0}}
def sign_order(pk,maker,tid,side,price,size):
 expire=int(time.time())+120;salt=int(time.time()*1000000)%(2**32)
 msg=eip712(maker,tid,price,size,side,expire,salt);acct=Account.from_key(pk)
 try:signed=acct.sign_typed_data(msg);sig="0x"+signed.signature.hex()
 except:from eth_account.messages import encode_typed_data;enc=encode_typed_data(msg);signed=Account.sign_message(enc,acct.key);sig="0x"+signed.signature.hex()
 return _post(f"{CLOB}/order",{"order":{"salt":str(msg["message"]["salt"]),"maker":maker,"signer":maker,"taker":"0x0000000000000000000000000000000000000000","tokenId":str(int(tid)),"makerAmount":str(msg["message"]["makerAmount"]),"takerAmount":str(msg["message"]["takerAmount"]),"expiration":str(expire),"nonce":"0","feeRateBps":"0","side":str(msg["message"]["side"]),"signatureType":0,"signature":sig},"owner":maker})
price_history={}
def scan():
 data=_get(f"{GAMMA}/markets?limit=500&closed=false&tag=5-minute")
 markets=data if isinstance(data,list)else data.get("data",data)if isinstance(data,dict)else[]
 if not isinstance(markets,list):markets=[]
 res={}
 for m in markets:
  try:
   slug=m.get("slug","").lower()
   if slug not in TARGET_SLUGS:continue
   name=TARGET_SLUGS[slug]
   cid=m.get("conditionId","")
   tids_raw=m.get("clobTokenIds","[]");tids=json.loads(tids_raw)if isinstance(tids_raw,str)else(tids_raw or[])
   if len(tids)<2 or not cid:continue
   yb=_get(f"{CLOB}/book?token_id={tids[0]}");nb=_get(f"{CLOB}/book?token_id={tids[1]}")
   if not yb or not nb:continue
   ya=yb.get("asks",[]);ybids=yb.get("bids",[]);na=nb.get("asks",[]);nbids=nb.get("bids",[])
   if not ya or not na:continue
   yes_a=float(ya[0]["price"]);yes_b=float(ybids[0]["price"])if ybids else yes_a*0.98
``` (1/3)
