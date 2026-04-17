import asyncio
from src.core.register import RegistrationEngine

async def run():
    engine = RegistrationEngine(email_service_type="cloudmail")
    try:
        result = await engine.run()
        print(f"Registration Result: {result}")
    except Exception as e:
        print(f"Error during registration: {e}")

if __name__ == "__main__":
    asyncio.run(run())
