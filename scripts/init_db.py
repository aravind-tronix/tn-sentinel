import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from local_server.db.models import init_db

if __name__ == "__main__":
    asyncio.run(init_db())
    print("Database schema created successfully.")
