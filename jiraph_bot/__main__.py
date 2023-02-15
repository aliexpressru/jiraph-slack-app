import asyncio
import logging
from dotenv import load_dotenv
from jiraph_bot.jiraph import Jiraph

async def main():
    load_dotenv('.env')
    logging.basicConfig(level=logging.INFO)
    jiraph = Jiraph()
    await jiraph.start()

if __name__ == '__main__':
    asyncio.run(main())
