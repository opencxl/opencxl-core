"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from asyncio import create_task, gather
from opencxl.util.component import RunnableComponent
from opencxl.cxl.device.root_port_device import CxlRootPortDevice
from opencxl.cxl.component.connection_client import ConnectionClient
from opencxl.cxl.component.cxl_component import CXL_COMPONENT_TYPE


class HostClient(RunnableComponent):
    def __init__(
        self,
        port_index: int,
        host: str = "0.0.0.0",
        port: int = 8000,
    ):
        label = f"Port{port_index}"
        super().__init__(label)
        self._connection_client = ConnectionClient(
            port_index, CXL_COMPONENT_TYPE.R, host=host, port=port
        )
        self._root_port_device = CxlRootPortDevice(
            downstream_connection=self._connection_client.get_cxl_connection(),
            label=label,
        )

    async def _run(self):
        tasks = [
            create_task(self._connection_client.run()),
            create_task(self._root_port_device.run()),
        ]
        await self._change_status_to_running()
        await gather(*tasks)

    async def _stop(self):
        tasks = [
            create_task(self._connection_client.stop()),
            create_task(self._root_port_device.stop()),
        ]
        await gather(*tasks)
