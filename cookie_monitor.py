import json
import time
import requests
import websocket

def monitor():
    try:
        resp = requests.get("http://127.0.0.1:9222/json")
        tabs = resp.json()
        ws_url = tabs[0]['webSocketDebuggerUrl']
        ws = websocket.create_connection(ws_url)
        
        def call(method, params={}):
            msg_id = int(time.time() * 1000)
            ws.send(json.dumps({"id": msg_id, "method": method, "params": params}))
            return json.loads(ws.recv())

        call("Network.enable")
        print("[*] 开始实时监控 Cookie 变化...")
        
        last_cookies = set()
        
        while True:
            # 轮询获取所有 Cookies (包括 HttpOnly)
            res = call("Network.getAllCookies")
            if "result" in res:
                cookies = res['result']['cookies']
                for c in cookies:
                    val = c['value']
                    name = c['name']
                    if "oaistb" in val or "oaistb" in name:
                        sig = f"{name}={val[:20]}..."
                        if sig not in last_cookies:
                            print(f"\n🔥 [发现 RT!] Time: {time.strftime('%H:%M:%S')}")
                            print(f"Name: {name}")
                            print(f"Value: {val}")
                            print(f"Domain: {c['domain']}")
                            last_cookies.add(sig)
            
            time.sleep(1)
            
    except Exception as e:
        print(f"[!] 错误: {e}")

if __name__ == "__main__":
    monitor()
