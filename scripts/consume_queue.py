import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from local_server.pipeline.queue_worker import main

if __name__ == "__main__":
    asyncio.run(main())
