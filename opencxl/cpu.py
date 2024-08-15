"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Optional, List, Tuple
from enum import Enum, auto
from opencxl.util.component import RunnableComponent
from opencxl.cxl.component.cxl_memory_hub import CxlMemoryHub


class CPU(RunnableComponent):
    def __init__(self, cxl_mem_hub: CxlMemoryHub, app):
        self._cxl_mem_hub = cxl_mem_hub
        self._application = app

    async def load(self, addr: int, size: int) -> int:
        return await self._cxl_mem_hub.load(addr, size)

    async def store(self, addr: int, size: int, value: int):
        await self._cxl_mem_hub.store(addr, size, value)
