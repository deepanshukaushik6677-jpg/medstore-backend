"""Bootstrap the very first PlatformAdmin account. Admins can't self-register
(there's no public signup for platform-level access), so this is run once by
whoever operates the platform: `python -m app.seed "Name" "email" "password"`.
"""
import asyncio
import sys

from .database import AsyncSessionLocal
from .models import PlatformAdmin
from .security import hash_password


async def main(name: str, email: str, password: str) -> None:
    async with AsyncSessionLocal() as db:
        admin = PlatformAdmin(name=name, email=email, password_hash=hash_password(password))
        db.add(admin)
        await db.commit()
        print(f"Created platform admin: {email}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print('Usage: python -m app.seed "Full Name" "email@example.com" "password"')
        raise SystemExit(1)
    asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3]))
