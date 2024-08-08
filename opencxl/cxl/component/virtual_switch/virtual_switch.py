"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from asyncio import gather, create_task
from dataclasses import dataclass
from enum import IntEnum
from typing import List, cast, Callable, Coroutine, Any

from opencxl.util.logger import logger
from opencxl.cxl.component.cxl_connection import CxlConnection
from opencxl.cxl.component.cxl_component import CXL_COMPONENT_TYPE
from opencxl.cxl.component.virtual_switch.port_binder import PortBinder, BIND_STATUS
from opencxl.cxl.component.virtual_switch.routers import CxlMemRouter, CxlIoRouter
from opencxl.cxl.component.virtual_switch.routing_table import RoutingTable
from opencxl.cxl.device.port_device import CxlPortDevice
from opencxl.cxl.device.upstream_port_device import UpstreamPortDevice
from opencxl.cxl.device.downstream_port_device import DownstreamPortDevice, DummyConfig
from opencxl.pci.device.pci_device import PciDevice
from opencxl.util.component import RunnableComponent


class PPB_BINDING_STATUS(IntEnum):
    UNBOUND = 0x00
    BIND_OR_UNBIND_IN_PROGRESS = 0x01
    BOUND_PHYSICAL_PORT = 0x02
    BOUND_LD = 0x03


@dataclass
class SwitchUpdateEvent:
    vcs_id: int
    vppb_id: int
    binding_status: PPB_BINDING_STATUS


AsyncEventHandlerType = Callable[[SwitchUpdateEvent], Coroutine[Any, Any, None]]


class VCS_STATE(IntEnum):
    DISABLED = 0x00
    ENABLED = 0x01
    INVALID_VCS_ID = 0xFF


class CxlVirtualSwitch(RunnableComponent):
    def __init__(
        self,
        id: int,
        upstream_port_index: int,
        vppb_counts: int,
        initial_bounds: List[int],
        physical_ports: List[CxlPortDevice],
    ):
        super().__init__()
        self._id = id
        self._vppb_counts = vppb_counts
        self._initial_bounds = initial_bounds
        self._physical_ports = physical_ports
        self._routing_table = RoutingTable(vppb_counts, label=f"VCS{id}")
        self._event_handler = None

        if len(initial_bounds) != self._vppb_counts:
            raise Exception("length of initial_bounds and vppb_count must be the same")

        # NOTE: Selects USP device based on initially provided upstream port index
        # The assigned USP will not be remapped later.
        if upstream_port_index < 0 or upstream_port_index >= len(self._physical_ports):
            raise Exception("Upstream Port Index is out of bound")

        port_device = self._physical_ports[upstream_port_index]
        if port_device.get_device_type() != CXL_COMPONENT_TYPE.USP:
            raise Exception(f"physical port {upstream_port_index} is not USP")
        self._usp_device = cast(UpstreamPortDevice, port_device)
        self._usp_device.set_routing_table(self._routing_table)
        self._usp_connection = self._usp_device.get_downstream_connection()
        self._vppb_connections = [CxlConnection() for _ in range(vppb_counts)]

        # NOTE: Make dummy DSP devices
        self._dummy_dsp_devices: List[DownstreamPortDevice] = []
        self._dummy_ep_devices: List[PciDevice] = []
        for vppb_index in range(self._vppb_counts):
            cxl_connection = CxlConnection()

            # NOTE: Create DSP device
            dummy_config = DummyConfig(
                vcs_id=self._id, vppb_id=vppb_index, routing_table=self._routing_table
            )
            dummy_dsp_device = DownstreamPortDevice(
                transport_connection=cxl_connection, dummy_config=dummy_config
            )
            self._dummy_dsp_devices.append(dummy_dsp_device)

            # NOTE: Create EP DEVICE
            label = f"VCS{self._id}:vPPB{vppb_index}(EP)"
            dummy_ep_device = PciDevice(cxl_connection, bar_size=4096, label=label)
            self._dummy_ep_devices.append(dummy_ep_device)

        # NOTE: Make PortBinder
        self._port_binder = PortBinder(self._id, self._vppb_connections)

        # NOTE: Make Routers
        self._cxl_io_router = CxlIoRouter(
            self._id, self._routing_table, self._usp_connection, self._vppb_connections
        )
        self._cxl_mem_router = CxlMemRouter(
            self._id, self._routing_table, self._usp_connection, self._vppb_connections
        )

    def _create_message(self, message: str):
        message = f"[{self.__class__.__name__} {self._id}] {message}"
        return message

    async def _start_dummy_devices(self):
        tasks = []
        for dummy_dsp in self._dummy_dsp_devices:
            tasks.append(dummy_dsp.run())
        for dummy_ep in self._dummy_ep_devices:
            tasks.append(dummy_ep.run())
        await gather(*tasks)

    async def _stop_dummy_devices(self):
        tasks = []
        for dummy_dsp in self._dummy_dsp_devices:
            tasks.append(dummy_dsp.stop())
        for dummy_ep in self._dummy_ep_devices:
            tasks.append(dummy_ep.stop())
        await gather(*tasks)

    async def _bind_initial_vppb(self):
        for vppb_index, port_index in enumerate(self._initial_bounds):
            if port_index == -1:
                await self.unbind_vppb(vppb_index)
            else:
                await self.bind_vppb(port_index, vppb_index)

    async def _run(self):
        await self._bind_initial_vppb()
        run_tasks = [
            create_task(self._start_dummy_devices()),
            create_task(self._cxl_io_router.run()),
            create_task(self._cxl_mem_router.run()),
            create_task(self._port_binder.run()),
        ]
        wait_tasks = [
            create_task(self._cxl_io_router.wait_for_ready()),
            create_task(self._cxl_mem_router.wait_for_ready()),
            create_task(self._port_binder.wait_for_ready()),
        ]
        await gather(*wait_tasks)
        await self._change_status_to_running()
        await gather(*run_tasks)

    async def _stop(self):
        tasks = [
            create_task(self._stop_dummy_devices()),
            create_task(self._cxl_io_router.stop()),
            create_task(self._cxl_mem_router.stop()),
            create_task(self._port_binder.stop()),
        ]
        await gather(*tasks)

    async def bind_vppb(self, port_index: int, vppb_index: int):
        if port_index < 0 or port_index >= len(self._physical_ports):
            raise Exception("port_index is out of bound")

        port_device = self._physical_ports[port_index]
        if port_device.get_device_type() != CXL_COMPONENT_TYPE.DSP:
            raise Exception(f"physical port {port_index} is not DSP")
        logger.info(
            self._create_message(f"Started Binding physical port {port_index} to vPPB {vppb_index}")
        )
        dsp_device = cast(DownstreamPortDevice, port_device)
        dsp_device.register_vppb(vppb_index)
        dsp_device.set_routing_table(self._routing_table, vppb_index)
        dsp_device.set_vppb_index(vppb_index)
        await self._call_event_handler(vppb_index, PPB_BINDING_STATUS.BIND_OR_UNBIND_IN_PROGRESS)
        await self._port_binder.bind_vppb(dsp_device, vppb_index)
        logger.info(
            self._create_message(
                f"Succcessfully bound physical port {port_index} to vPPB {vppb_index}"
            )
        )
        await self._call_event_handler(vppb_index, PPB_BINDING_STATUS.BOUND_LD)

    async def unbind_vppb(self, vppb_index: int):
        if vppb_index >= len(self._dummy_dsp_devices):
            raise Exception("vppb_index is out of bound")
        logger.info(self._create_message(f"Started unbinding physical port from vPPB {vppb_index}"))
        dummy_device = self._dummy_dsp_devices[vppb_index]
        await self._call_event_handler(vppb_index, PPB_BINDING_STATUS.BIND_OR_UNBIND_IN_PROGRESS)
        await self._port_binder.unbind_vppb(dummy_device, vppb_index)
        logger.info(
            self._create_message(f"Succcessfully unbound physical port from vPPB {vppb_index}")
        )
        await self._call_event_handler(vppb_index, PPB_BINDING_STATUS.UNBOUND)

    async def _call_event_handler(self, vppb_id: int, binding_status: PPB_BINDING_STATUS):
        if not self._event_handler:
            return
        event = SwitchUpdateEvent(vcs_id=self._id, vppb_id=vppb_id, binding_status=binding_status)
        await self._event_handler(event)

    def get_vppb_counts(self) -> int:
        return self._vppb_counts

    def get_bound_vppb_counts(self) -> int:
        return self._port_binder.get_bound_vppbs_count()

    def is_vppb_bound(self, vppb_index) -> bool:
        if vppb_index >= self._vppb_counts:
            raise Exception("vppb_index is out of bound")
        return self._port_binder.get_bind_status(vppb_index) == BIND_STATUS.BOUND

    def get_usp_port_id(self) -> int:
        return self._usp_device.get_port_index()

    def get_bound_port_id(self, vppb_id: int) -> int:
        return self._port_binder.get_bound_port_id(vppb_id)

    def register_event_handler(self, event_handler: AsyncEventHandlerType):
        self._event_handler = event_handler
