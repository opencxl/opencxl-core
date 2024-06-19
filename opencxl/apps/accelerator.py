"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from asyncio import gather, create_task

from opencxl.util.logger import logger
from opencxl.util.component import RunnableComponent
from opencxl.cxl.device.cxl_type1_device import CxlType1Device
from opencxl.cxl.device.cxl_type2_device import CxlType2Device
from opencxl.cxl.component.switch_connection_client import SwitchConnectionClient
from opencxl.cxl.component.cxl_component import CXL_COMPONENT_TYPE


# Example devices based on type1 and type2 devices


class MyType1Accelerator(RunnableComponent):
    def __init__(
        self,
        port_index: int,
        host: str = "0.0.0.0",
        port: int = 8000,
    ):
        label = f"Port{port_index}"
        super().__init__(label)
        self._sw_conn_client = SwitchConnectionClient(
            port_index, CXL_COMPONENT_TYPE.T1, host=host, port=port
        )
        self._cxl_type1_device = CxlType1Device(
            transport_connection=self._sw_conn_client.get_cxl_connection(),
            label=label,
        )

    async def _run_app(self, *args):
        # example app: prints the arguments
        for idx, arg in enumerate(args):
            logger.info(self._create_message(f"Type 1 Accelerator: {idx},{arg}"))

    async def _run(self):
        tasks = [
            create_task(self._sw_conn_client.run()),
            create_task(self._cxl_type1_device.run()),
        ]
        await self._sw_conn_client.wait_for_ready()
        await self._cxl_type1_device.wait_for_ready()
        tasks.append(create_task(self._run_app(1, 2)))
        await self._change_status_to_running()
        await gather(*tasks)

    async def _stop(self):
        tasks = [
            create_task(self._sw_conn_client.stop()),
            create_task(self._cxl_type1_device.stop()),
        ]
        await gather(*tasks)


class MyType2Accelerator(RunnableComponent):
    def __init__(
        self,
        port_index: int,
        memory_size: int,
        memory_file: str,
        host: str = "0.0.0.0",
        port: int = 8000,
    ):
        label = f"Port{port_index}"
        super().__init__(label)
        self._sw_conn_client = SwitchConnectionClient(
            port_index, CXL_COMPONENT_TYPE.T2, host=host, port=port
        )
        self._cxl_type2_device = CxlType2Device(
            transport_connection=self._sw_conn_client.get_cxl_connection(),
            memory_size=memory_size,
            memory_file=memory_file,
            label=label,
        )

    async def _run_app(self, *args):
        # example app: prints the arguments
        for idx, arg in enumerate(args):
            logger.info(self._create_message(f"Type 2 Accelerator: {idx},{arg}"))

    async def _run(self):
        tasks = [
            create_task(self._sw_conn_client.run()),
            create_task(self._cxl_type2_device.run()),
        ]
        await self._sw_conn_client.wait_for_ready()
        await self._cxl_type2_device.wait_for_ready()
        tasks.append(create_task(self._run_app(1, 2, 3, 4)))
        await self._change_status_to_running()
        await gather(*tasks)

    async def _stop(self):
        tasks = [
            create_task(self._sw_conn_client.stop()),
            create_task(self._cxl_type2_device.stop()),
        ]
        await gather(*tasks)
