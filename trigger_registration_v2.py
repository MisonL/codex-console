import asyncio
import json
from src.core.register import RegistrationEngine
from src.services.cloud_mail import CloudMailService
from src.database.session import init_database, get_db
from src.database import crud

async def run():
    init_database()
    with get_db() as db:
        svc = crud.get_email_service_by_id(db, 1)
        conf = svc.config
        if isinstance(conf, str):
            conf = json.loads(conf)
        email_service = CloudMailService(conf)
        
        # 正确初始化 RegistrationEngine: email_service 实例
        engine = RegistrationEngine(email_service=email_service)
        
        # 强制设置 native 流程以触发 anyauto 引擎
        engine.registration_entry_flow = "native"
        
        print(f"--- 启动 AnyAuto 注册流程 ---")
        result = engine.run()
        
        if result.success:
            print(f"✅ 注册成功!")
            print(f"Email: {result.email}")
            print(f"AT: {result.access_token[:30]}...")
            print(f"RT: {result.refresh_token or '-'}")
            print(f"ST: {result.session_token[:30]}...")
        else:
            print(f"❌ 注册失败: {result.error_message}")

if __name__ == "__main__":
    asyncio.run(run())
