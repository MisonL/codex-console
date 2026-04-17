
import asyncio
import sys
import os

# 将 src 目录加入路径
sys.path.append(os.getcwd())

from src.database.session import init_database, get_db
from src.database.models import Account, EmailService
from sqlalchemy import select
from src.core.openai.codex_auth_workbench import (
    CodexAuthEngine, 
    persist_codex_auth_success, 
    resolve_email_service_for_account
)

async def repair_account(account_id: int):
    print(f"[*] 正在初始化数据库并准备修复账号 ID: {account_id}...")
    init_database()
    
    with get_db() as session:
        # 1. 获取账号信息
        acc = session.get(Account, account_id)
        if not acc:
            print(f"[!] 未找到账号 ID: {account_id}")
            return
        
        print(f"[*] 目标账号: {acc.email}")
        
        # 2. 获取所有邮箱服务以备解析
        email_services = session.execute(select(EmailService)).scalars().all()
        
        # 3. 解析该账号对应的邮箱服务实例
        service_instance, error = resolve_email_service_for_account(acc, email_services)
        if not service_instance:
            print(f"[!] 无法解析邮箱服务: {error}")
            return
        
        print(f"[*] 已绑定邮箱服务: {acc.email_service}")
        
        # 4. 初始化修复引擎
        engine = CodexAuthEngine(
            email=acc.email,
            password=acc.password,
            email_service=service_instance,
            proxy_url=acc.proxy_used,
            callback_logger=lambda msg: print(f"  [LOG] {msg}")
        )
        
        # 5. 执行修复
        print("[*] 正在执行 OAuth 修复流程 (这可能需要 1-2 分钟)...")
        result = await asyncio.to_thread(engine.run)
        
        if result.success:
            print(" ✅ 修复成功! 正在保存结果到数据库...")
            persist_codex_auth_success(acc, result)
            session.commit()
            print(f"[*] 账号 {acc.email} 现已进入健康状态。")
        else:
            print(f" ❌ 修复失败: {result.error_message}")
            if result.block_reason:
                print(f" [!] 封禁/阻断原因: {result.block_reason}")

if __name__ == "__main__":
    # 修复目标 ID 104
    target_id = 104
    asyncio.run(repair_account(target_id))
