import asyncio
from opencxl.apps.cxl_complex_host import CxlComplexHost
from opencxl.cpu import CPU
from opencxl.util.logger import logger


async def sample_app(cpu: CPU, value: str):
    logger.info(f"{value} I AM HERE!")
    await cpu.store(0x100000000000, 0x40, 0xDEADBEEF)
    val = await cpu.load(0x100000000000, 0x40)
    logger.info(f"0x{val:X}")
    val = await cpu.load(0x100000000040, 0x40)
    logger.info(f"0x{val:X}")


async def main():
    host = CxlComplexHost(0, 256 * 1024 * 1024, sample_app)
    logger.info("STARTING")
    await host.run()


if __name__ == "__main__":
    asyncio.run(main())
