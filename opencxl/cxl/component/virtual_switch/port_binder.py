"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from asyncio import Queue, create_task, gather
from dataclasses import dataclass
from enum import Enum, auto
from typing import List, Optional

from opencxl.cxl.component.cxl_connection import CxlConnection
from opencxl.cxl.component.virtual_switch.vppb import Vppb
from opencxl.cxl.component.virtual_switch.upstream_vppb import UpstreamVppb
from opencxl.cxl.component.virtual_switch.downstream_vppb import DownstreamVppb
from opencxl.cxl.device.downstream_port_device import DownstreamPortDevice
from opencxl.util.async_gatherer import AsyncGatherer
from opencxl.util.component import RunnableComponent


@dataclass
class BindPair:
    source: Queue
    destination: Queue


class BindProcessor(RunnableComponent):
    def __init__(
        self,
        vcs_id: int,
        vppb_id: int,
        downsteam_connection: CxlConnection,
        upstream_connection: CxlConnection,
    ):
        super().__init__()
        self._vcs_id = vcs_id
        self._vppb_id = vppb_id
        self._dsc = downsteam_connection
        self._usc = upstream_connection

        self._pairs = [
            BindPair(self._dsc.cfg_fifo.host_to_target, self._usc.cfg_fifo.host_to_target),
            BindPair(self._usc.cfg_fifo.target_to_host, self._dsc.cfg_fifo.target_to_host),
            BindPair(self._dsc.mmio_fifo.host_to_target, self._usc.mmio_fifo.host_to_target),
            BindPair(self._usc.mmio_fifo.target_to_host, self._dsc.mmio_fifo.target_to_host),
            BindPair(
                self._dsc.cxl_mem_fifo.host_to_target,
                self._usc.cxl_mem_fifo.host_to_target,
            ),
            BindPair(
                self._usc.cxl_mem_fifo.target_to_host,
                self._dsc.cxl_mem_fifo.target_to_host,
            ),
            BindPair(
                self._dsc.cxl_cache_fifo.host_to_target,
                self._usc.cxl_cache_fifo.host_to_target,
            ),
            BindPair(
                self._usc.cxl_cache_fifo.target_to_host,
                self._dsc.cxl_cache_fifo.target_to_host,
            ),
        ]

    def _create_message(self, message):
        message = f"[{self.__class__.__name__}:VCS{self._vcs_id}:vPPB{self._vppb_id}] {message}"
        return message

    async def _process(self, source: Queue, destination: Queue):
        while True:
            packet = await source.get()
            if packet is None:
                break
            await destination.put(packet)

    async def _run(self):
        tasks = []
        for pair in self._pairs:
            task = create_task(self._process(pair.source, pair.destination))
            tasks.append(task)
        await self._change_status_to_running()
        await gather(*tasks)

    async def _stop(self):
        for pair in self._pairs:
            await pair.source.put(None)


class BIND_STATUS(Enum):
    INIT = auto()
    BOUND = auto()
    UNBOUND = auto()


@dataclass
class BindSlot:
    vppb: Vppb
    status: BIND_STATUS = BIND_STATUS.INIT
    processor: Optional[BindProcessor] = None
    dsp: Optional[DownstreamPortDevice] = None


class PortBinder(RunnableComponent):
    def __init__(self, vcs_id: int, vppbs: List[Vppb]):
        super().__init__()
        self._vcs_id = vcs_id
        self._vppbs = vppbs
        self._bind_slots: List[BindSlot] = []
        self._async_gatherer = AsyncGatherer()
        for vppb in self._vppbs:
            bind_slot = BindSlot(
                vppb=vppb,
            )
            self._bind_slots.append(bind_slot)

        self._dummy = BindProcessor(self._vcs_id, 0, CxlConnection(), CxlConnection())
        self._init_flag = True

    def _create_message(self, message):
        message = f"[{self.__class__.__name__}:VCS{self._vcs_id}] {message}"
        return message

    async def bind_vppb(self, dsp_device: DownstreamPortDevice, vppb_index: int):
        if self._init_flag:
            self._async_gatherer.add_task(self._dummy.run())
            self._init_flag = False
        if vppb_index >= len(self._bind_slots) or vppb_index < 0:
            raise Exception("vppb_index is out of bound")

        bind_slot = self._bind_slots[vppb_index]
        if bind_slot.status == BIND_STATUS.BOUND:
            raise Exception(f"vPPB[{vppb_index}] is already bound")

        # TODO: Get config space from dummy and store in PPB
        if bind_slot.processor is not None:
            await bind_slot.processor.stop()

        bind_slot.dsp = dsp_device
        bind_slot.vppb = self._vppbs[vppb_index]
        downstream_connection = bind_slot.vppb.get_downstream_connection()
        # upstream_connection = dsp_device.get_transport_connection()
        upstream_connection = dsp_device.get_ppb_device().get_upstream_connection()
        bind_slot.processor = BindProcessor(
            self._vcs_id, vppb_index, downstream_connection, upstream_connection
        )
        self._async_gatherer.add_task(bind_slot.processor.run())
        bind_slot.status = BIND_STATUS.BOUND

    # TODO: vppb -- unbind need to be fiexed
    async def unbind_vppb(self, vppb_index: int):
        if self._init_flag:
            self._async_gatherer.add_task(self._dummy.run())
            self._init_flag = False
        if vppb_index >= len(self._bind_slots) or vppb_index < 0:
            raise Exception("vppb_index is out of bound")

        bind_slot = self._bind_slots[vppb_index]
        if bind_slot.status == BIND_STATUS.UNBOUND:
            raise Exception(f"vPPB[{vppb_index}] is already unbound")

        # TODO: Get config space from PPB and store in dummy
        if bind_slot.processor is not None:
            await bind_slot.processor.stop()
        bind_slot.status = BIND_STATUS.UNBOUND

    def get_bind_status(self, vppb_index: int) -> BIND_STATUS:
        if vppb_index >= len(self._bind_slots) or vppb_index < 0:
            raise Exception("vppb_index is out of bound")
        return self._bind_slots[vppb_index].status

    def get_bound_vppbs_count(self) -> int:
        bound_vppbs = 0
        for slot in self._bind_slots:
            if slot.status == BIND_STATUS.BOUND:
                bound_vppbs += 1
        return bound_vppbs

    def get_bound_port_id(self, vppb_index: int) -> int:
        if vppb_index >= len(self._bind_slots) or vppb_index < 0:
            raise Exception("vppb_index is out of bound")
        if self._bind_slots[vppb_index].status != BIND_STATUS.BOUND:
            raise Exception(f"vPPB{vppb_index} is not bound")
        return self._bind_slots[vppb_index].dsp.get_port_index()

    def get_bind_slots(self):
        return self._bind_slots

    def get_vppbs(self):
        return self._vppbs

    async def _run(self):
        await self._change_status_to_running()
        await self._async_gatherer.wait_for_completion()

    async def _stop(self):
        tasks = []
        for slot in self._bind_slots:
            if slot.processor is not None:
                tasks.append(create_task(slot.processor.stop()))
        tasks.append(create_task(self._dummy.stop()))
        await gather(*tasks)
