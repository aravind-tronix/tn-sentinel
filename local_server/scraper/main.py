import asyncio
from .scheduler import run_all_sources, scheduler_loop

async def main() -> None:
    print("[scraper] initial scrape pass")
    results = await run_all_sources()
    for result in results:
        print(f"[scraper] {result['source_id']} pushed={result['pushed']} filtered={result['filtered']}")

    print("[scraper] entering scheduler loop")
    await scheduler_loop()

if __name__ == "__main__":
    asyncio.run(main())
