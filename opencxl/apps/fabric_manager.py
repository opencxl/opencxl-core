"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from asyncio import create_task, gather
import traceback
from opencxl.cxl.component.mctp.mctp_connection_manager import (
    MctpConnectionManager,
)
from opencxl.cxl.component.mctp.mctp_cci_api_client import (
    MctpCciApiClient,
    GetPhysicalPortStateRequestPayload,
    GetVirtualCxlSwitchInfoRequestPayload,
    BindVppbRequestPayload,
    UnbindVppbRequestPayload,
)
from opencxl.cxl.component.fabric_manager.socketio_server import (
    FabricManagerSocketIoServer,
)
from opencxl.util.component import RunnableComponent
from opencxl.util.logger import logger


class CxlFabricManager(RunnableComponent):
    def __init__(
        self,
        mctp_host: str = "0.0.0.0",
        mctp_port: int = 8100,
        socketio_host: str = "0.0.0.0",
        socketio_port: int = 8200,
        use_test_runner: bool = False,
    ):
        super().__init__()
        self._connection_manager = MctpConnectionManager(mctp_host, mctp_port)
        self._api_client = MctpCciApiClient(self._connection_manager.get_mctp_connection())
        self._socketio_server = FabricManagerSocketIoServer(
            self._api_client, socketio_host, socketio_port
        )
        self._use_test_runner = use_test_runner

    async def _run_test(self):
        try:
            await self._api_client.identify_switch_device()
            await self._api_client.get_physical_port_state(
                GetPhysicalPortStateRequestPayload([0, 1, 2, 3, 4])
            )
            await self._api_client.get_virtual_cxl_switch_info(
                GetVirtualCxlSwitchInfoRequestPayload(
                    start_vppb=0, vppb_list_limit=255, vcs_id_list=[0]
                )
            )
            await self._api_client.get_connected_devices()
            await self._api_client.unbind_vppb(UnbindVppbRequestPayload(vcs_id=0, vppb_id=0))
            await self._api_client.unbind_vppb(UnbindVppbRequestPayload(vcs_id=0, vppb_id=1))
            await self._api_client.unbind_vppb(UnbindVppbRequestPayload(vcs_id=0, vppb_id=2))
            await self._api_client.unbind_vppb(UnbindVppbRequestPayload(vcs_id=0, vppb_id=3))
            await self._api_client.bind_vppb(
                BindVppbRequestPayload(vcs_id=0, vppb_id=0, physical_port_id=1)
            )
            await self._api_client.bind_vppb(
                BindVppbRequestPayload(vcs_id=0, vppb_id=1, physical_port_id=2)
            )
            await self._api_client.bind_vppb(
                BindVppbRequestPayload(vcs_id=0, vppb_id=2, physical_port_id=3)
            )
            await self._api_client.bind_vppb(
                BindVppbRequestPayload(vcs_id=0, vppb_id=3, physical_port_id=4)
            )
        except Exception as e:
            logger.debug(str(e))
            logger.debug(traceback.format_exc())

    async def _run(self):
        tasks = [
            create_task(self._connection_manager.run()),
            create_task(self._socketio_server.run()),
            create_task(self._api_client.run()),
        ]
        wait_tasks = [
            create_task(self._connection_manager.wait_for_ready()),
            create_task(self._socketio_server.wait_for_ready()),
            create_task(self._api_client.wait_for_ready()),
        ]
        if self._use_test_runner:
            tasks.append(create_task(self._run_test()))
        await gather(*wait_tasks)
        await self._change_status_to_running()
        await gather(*tasks)

    async def _stop(self):
        await self._connection_manager.stop()
        await self._socketio_server.stop()
        await self._api_client.stop()
