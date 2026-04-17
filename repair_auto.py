
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
    resolve_email_service_for_account,
    resolve_codex_auth_status,
    CODEX_AUTH_REPAIRABLE
)

async def repair_latest_repairable():
    print("[*] 正在搜索可修复的账号...")
    init_database()
    
    with get_db() as session:
        # 1. 获取所有账号
        result = session.execute(select(Account).order_by(Account.id.desc()))
        accounts = result.scalars().all()
        
        target_acc = None
        for acc in accounts:
            status = resolve_codex_auth_status(acc)
            if status.health == CODEX_AUTH_REPAIRABLE:
                target_acc = acc
                break
        
        if not target_acc:
            print("[!] 未找到任何处于 '可修复' 状态的账号。")
            return
        
        print(f"[*] 锁定目标账号: {target_acc.email} (ID: {target_acc.id})")
        
        # 2. 获取所有邮箱服务
        email_services = session.execute(select(EmailService)).scalars().all()
        
        # 3. 解析该账号对应的邮箱服务实例
        service_instance, error = resolve_email_service_for_account(target_acc, email_services)
        if not service_instance:
            print(f"[!] 无法解析邮箱服务: {error}")
            return
        
        # 4. 初始化修复引擎
        engine = CodexAuthEngine(
            email=target_acc.email,
            password=target_acc.password,
            email_service=service_instance,
            proxy_url=target_acc.proxy_used,
            callback_logger=lambda msg: print(f"  [LOG] {msg}")
        )
        
        # 5. 执行修复
        print("[*] 正在执行修复流程...")
        result = await asyncio.to_thread(engine.run)
        
        if result.success:
            print(" ✅ 修复成功! 正在保存结果...")
            persist_codex_auth_success(target_acc, result)
            session.commit()
            print(f"[*] 账号 {target_acc.email} 现已进入健康状态。")
        else:
            print(f" ❌ 修复失败: {result.error_message}")

if __name__ == "__main__":
    asyncio.run(repair_latest_repairable())
