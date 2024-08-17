import asyncio
from dataclasses import dataclass, field
from typing import Optional, List, Tuple
from enum import Enum, auto
from opencxl.util.component import RunnableComponent
from opencxl.cxl.component.cxl_memory_hub import CxlMemoryHub
from opencxl.apps.cxl_complex_host import CxlComplexHost
from opencxl.cpu import CPU
from opencxl.util.logger import logger


async def sample_app(cpu: CPU, value: str):
    logger.info(f"{value} I AM HERE!")


async def main():
    host = CxlComplexHost(0, 256 * 1024 * 1024, sample_app)
    logger.info("STARTING")
    await host.run()


if __name__ == "__main__":
    asyncio.run(main())
