
import sys
import os
import asyncio
import logging

# 配置日志输出到终端
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s][%(levelname)s] %(message)s',
    stream=sys.stdout
)

# 添加项目路径
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from src.core.register import RegistrationEngine
from src.database.session import get_db, init_database
from src.database import crud
from src.services import EmailServiceFactory, EmailServiceType

async def run_final_validation():
    print("="*60)
    print("开始全流程注册链路最终验证 (Target: cloud-mail, Flow: native)")
    print("="*60)
    
    try:
        # 初始化数据库连接
        init_database("sqlite:///data/database.db")
        
        # 从数据库中获取真实的邮箱服务配置
        with get_db() as db:
            # 找到第一个启用的 cloudmail 服务
            all_services = crud.get_email_services(db)
            service_record = next((s for s in all_services if s.service_type == "cloudmail" and s.enabled), None)
            
            if not service_record:
                print("[ERROR] 数据库中未找到已启用的 cloud-mail 服务，请先在界面配置！")
                return

            print(f"[INFO] 找到已启用的邮箱服务: {service_record.name} (ID: {service_record.id})")
            
            # 使用工厂创建实例
            service = EmailServiceFactory.create(
                service_type=EmailServiceType.CLOUDMAIL,
                config=service_record.config,
                name=service_record.name
            )
            service.id = service_record.id
        
        print(f"[INFO] 成功初始化邮箱服务实例: {service.__class__.__name__}")
        
        # 初始化引擎
        engine = RegistrationEngine(service, "sg")
        
        # 覆盖日志记录函数，直接输出到控制台
        def custom_log(msg, level="info"):
            print(f"[{level.upper()}] {msg}")
        
        engine._log = custom_log
        
        # 启动任务
        print("[INFO] 正在执行 engine.run()...")
        result = engine.run()
        
        print("\n" + "="*60)
        print(f"验证完成! 成功状态: {result.success}")
        if result.success:
            print(f"新账号邮箱: {result.email}")
            print(f"Access Token: {result.access_token[:50] if result.access_token else 'None'}...")
            print(f"Refresh Token: {result.refresh_token[:50] if result.refresh_token else 'None'}...")
        else:
            print(f"报错信息: {result.error_message}")
        print("="*60)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"脚本执行崩溃: {e}")

if __name__ == "__main__":
    asyncio.run(run_final_validation())
