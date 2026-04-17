import sys
import os
import asyncio

# 添加 src 到 PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from src.core.register import RegistrationEngine
from src.database.init_db import initialize_database as init_db

def run_test():
    # init_db() 确保数据库就绪
    # init_db()
    # 实例化 RegistrationEngine，传入 'cloudmail'
    engine = RegistrationEngine("cloudmail", "sg")
    print("Starting registration engine test...")
    success = engine.run()
    print(f"Registration Result: {success}")
    if success:
        print(f"Email: {engine.email}, Password: {engine.password}")

if __name__ == "__main__":
    run_test()
