"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from asyncio import create_task, gather

from opencxl.util.logger import logger
from opencxl.util.component import RunnableComponent
from opencxl.cxl.component.bind_processor import PpbDspBindProcessor
from opencxl.cxl.component.cxl_connection import CxlConnection


class PpbDevice(RunnableComponent):
    def __init__(
        self,
        port_index: int = 0,
    ):

        super().__init__()
        self._port_index = port_index
        self._downstream_connection = CxlConnection()
        self._upstream_connection = CxlConnection()
        self._bind_processor = PpbDspBindProcessor(
            self._upstream_connection, self._downstream_connection
        )

    def _get_label(self) -> str:
        return f"PPB{self._port_index}"

    def _create_message(self, message: str) -> str:
        message = f"[{self.__class__.__name__}:{self._get_label()}] {message}"
        return message

    def get_upstream_connection(self) -> CxlConnection:
        return self._upstream_connection

    def get_downstream_connection(self) -> CxlConnection:
        return self._downstream_connection

    async def _run(self):
        logger.info(self._create_message("Starting"))
        run_tasks = [
            create_task(self._bind_processor.run()),
        ]
        wait_tasks = [
            create_task(self._bind_processor.wait_for_ready()),
        ]
        # pylint: disable=duplicate-code
        await gather(*wait_tasks)
        await self._change_status_to_running()
        await gather(*run_tasks)
        logger.info(self._create_message("Stopped"))

    async def _stop(self):
        logger.info(self._create_message("Stopping"))
        tasks = [
            create_task(self._bind_processor.stop()),
        ]
        await gather(*tasks)
