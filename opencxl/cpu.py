"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

import asyncio
from typing import Callable, Awaitable
from opencxl.util.component import RunnableComponent
from opencxl.cxl.component.cxl_memory_hub import CxlMemoryHub


class CPU(RunnableComponent):
    def __init__(self, cxl_mem_hub: CxlMemoryHub, app: Callable[[], Awaitable[None]]):
        super().__init__()
        self._cxl_mem_hub = cxl_mem_hub
        self._app = app

    async def load(self, addr: int, size: int) -> int:
        return await self._cxl_mem_hub.load(addr, size)

    async def store(self, addr: int, size: int, value: int):
        await self._cxl_mem_hub.store(addr, size, value)

    async def _app_run_task(self, **kwargs):
        return await self._app(cpu=self, **kwargs)

    async def _run(self):
        tasks = [
            asyncio.create_task(self._app_run_task(value="Value")),
        ]
        await self._change_status_to_running()
        await asyncio.gather(*tasks)

    async def _stop(self):
        # tasks = [
        #     asyncio.create_task(self._cxl_memory_hub.stop()),
        # ]
        # await asyncio.gather(*tasks)
        pass
