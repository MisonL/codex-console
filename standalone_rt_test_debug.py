import sys
print("[*] Starting imports...")
import asyncio
print("[*] asyncio loaded")
import json
print("[*] json loaded")
import os
print("[*] os loaded")

print("[*] loading AnyAutoRegistrationEngine...")
try:
    from src.core.anyauto.register_flow import AnyAutoRegistrationEngine
    print("[*] AnyAutoRegistrationEngine loaded")
except Exception as e:
    print(f"[!] AnyAutoRegistrationEngine load failed: {e}")

print("[*] loading CloudMailService...")
try:
    from src.services.cloud_mail import CloudMailService
    print("[*] CloudMailService loaded")
except Exception as e:
    print(f"[!] CloudMailService load failed: {e}")

async def run():
    print("[*] Inside run()")
    config = {
        "base_url": "https://email.431695.xyz",
        "admin_password": "admin",
        "domain": "email.431695.xyz"
    }
    
    # 尝试从数据库读取配置
    try:
        import sqlite3
        db_path = "data/database.db"
        if os.path.exists(db_path):
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT config FROM email_services WHERE service_type='cloudmail' LIMIT 1")
            row = cursor.fetchone()
            if row:
                config = json.loads(row[0])
                print("[*] Loaded config from DB")
            conn.close()
    except Exception as e:
        print(f"[!] DB read failed: {e}")

    email_service = CloudMailService(config)
    engine = AnyAutoRegistrationEngine(
        email_service=email_service,
        browser_mode="protocol"
    )
    
    print("🚀 Starting engine.run()...")
    # 设置 timeout 防止死锁
    try:
        result = engine.run()
        print(f"✅ Success: {result.get('success')}")
        if result.get('success'):
            print(f"RT: {result.get('refresh_token')}")
    except Exception as e:
        print(f"❌ Engine Error: {e}")

if __name__ == "__main__":
    asyncio.run(run())
