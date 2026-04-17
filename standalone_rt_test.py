import asyncio
import json
import os
import sys
from src.core.anyauto.register_flow import AnyAutoRegistrationEngine
from src.services.cloud_mail import CloudMailService

def log_msg(msg, level="info"):
    print(f"[{level.upper()}] {msg}")

async def run():
    # 这里的配置需要根据您的实际 cloudmail 填写，或者我尝试从环境变量读取
    # 假设我们使用一个通用的测试配置
    config = {
        "base_url": "https://email.431695.xyz",
        "admin_password": "admin", # 这是一个占位符，实际会从 db 读
        "domain": "email.431695.xyz"
    }
    
    # 尝试从数据库读取配置 (不启动整个引擎，只读一次)
    try:
        import sqlite3
        conn = sqlite3.connect("data/database.db")
        cursor = conn.cursor()
        cursor.execute("SELECT config FROM email_services WHERE service_type='cloudmail' LIMIT 1")
        row = cursor.fetchone()
        if row:
            config = json.loads(row[0])
            print("[*] 成功从数据库加载 CloudMail 配置")
        conn.close()
    except Exception as e:
        print(f"[!] 无法从数据库读取配置，使用默认: {e}")

    email_service = CloudMailService(config)
    
    # 初始化引擎
    engine = AnyAutoRegistrationEngine(
        email_service=email_service,
        callback_logger=log_msg,
        browser_mode="protocol"
    )
    
    print("="*60)
    print("🚀 启动独立 RT 捕获测试")
    print("="*60)
    
    # 运行
    result_dict = engine.run()
    
    print("\n" + "="*60)
    if result_dict.get("success"):
        print("✅ 最终结果: 成功")
        print(f"Email: {result_dict.get('email')}")
        print(f"RT: {result_dict.get('refresh_token') or '❌ MISSING'}")
        print(f"AT: {result_dict.get('access_token')[:30]}...")
    else:
        print(f"❌ 最终结果: 失败 - {result_dict.get('error_message')}")
    print("="*60)

if __name__ == "__main__":
    asyncio.run(run())
