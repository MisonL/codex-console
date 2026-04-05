import json
import time
import requests
import websocket
import uuid

# 1. 查找浏览器 Tab
try:
    resp = requests.get("http://127.0.0.1:9222/json")
    tabs = resp.json()
    target_tab = None
    for tab in tabs:
        if "chatgpt.com" in tab['url'] or "openai.com" in tab['url']:
            target_tab = tab
            break
    if not target_tab:
        target_tab = tabs[0]
    
    ws_url = target_tab['webSocketDebuggerUrl']
    print(f"[*] 成功连接到浏览器 Tab: {target_tab['url']}")
except Exception as e:
    print(f"[!] 无法连接到浏览器: {e}")
    exit(1)

# 2. 建立 WebSocket 连接
ws = websocket.create_connection(ws_url)

def send_cdp(method, params={}):
    msg_id = int(time.time() * 1000)
    ws.send(json.dumps({"id": msg_id, "method": method, "params": params}))
    return json.loads(ws.recv())

# 3. 开启网络监听
send_cdp("Network.enable")
print("[*] 网络监听已开启，正在注册中...")

# 记录所有包含 oaistb 的 Cookie
captured_data = []

def on_message(ws, message):
    data = json.loads(message)
    if data.get("method") == "Network.responseReceived":
        headers = data['params']['response']['headers']
        url = data['params']['response']['url']
        set_cookie = headers.get("set-cookie") or headers.get("Set-Cookie")
        if set_cookie and "oaistb" in set_cookie:
            print(f"\n🔥 [发现 RT!] URL: {url}")
            print(f"Header: {set_cookie}")
            captured_data.append({"url": url, "cookie": set_cookie, "time": time.time()})

# 这里我将手动一步步执行输入逻辑
# 由于 CDP 操作较多，我先实现一个简单的“输入邮箱”观察点
print("\n--- 实验指南 ---")
print("1. 请在刚才拉起的浏览器中手动输入邮箱并点击继续。")
print("2. 我会在此脚本中实时监控每一跳的 Header。")
print("3. 特别关注 /api/accounts/create_account 这一跳。")

# 启动监听循环
try:
    while True:
        msg = ws.recv()
        on_message(ws, msg)
except KeyboardInterrupt:
    print("[*] 实验结束，保存数据...")
    with open("rt_analysis.json", "w") as f:
        json.dump(captured_data, f)
