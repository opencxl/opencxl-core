"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from abc import abstractmethod
from asyncio import gather, create_task
from typing import List, Optional, cast

from opencxl.util.logger import logger
from opencxl.util.component import RunnableComponent
from opencxl.util.pci import bdf_to_string
from opencxl.util.number import tlptoh16
from opencxl.cxl.component.cxl_connection import CxlConnection, FifoPair
from opencxl.cxl.device.upstream_port_device import UpstreamPortDevice
from opencxl.cxl.component.virtual_switch.routing_table import RoutingTable
from opencxl.cxl.component.virtual_switch.port_binder import PortBinder, BindSlot
from opencxl.cxl.transport.transaction import (
    BasePacket,
    CxlCacheD2HReqPacket,
    CxlIoBasePacket,
    CxlIoCfgRdPacket,
    CxlIoCfgWrPacket,
    CxlIoCompletionPacket,
    CxlIoMemReqPacket,
    CxlIoCompletionWithDataPacket,
    CXL_IO_CPL_STATUS,
    CxlMemBasePacket,
    CxlMemM2SReqPacket,
    CxlMemM2SRwDPacket,
    CxlMemM2SBIRspPacket,
    CxlMemS2MBISnpPacket,
    CxlCacheBasePacket,
    CxlCacheH2DRspPacket,
    CxlCacheH2DReqPacket,
)


class CxlRouter(RunnableComponent):
    def __init__(
        self,
        vcs_id: int,
        routing_table: RoutingTable,
    ):
        self._downstream_connections: List[BindSlot]
        self._downstream_connection_fifos: List[FifoPair]
        self._upstream_connection_fifo: FifoPair

        super().__init__()
        self._vcs_id = vcs_id
        self._routing_table = routing_table

    def _create_message(self, message):
        message = f"[{self.__class__.__name__}:VCS{self._vcs_id}] {message}"
        return message

    @abstractmethod
    async def _process_host_to_target_packets(self):
        pass

    @abstractmethod
    async def _process_target_to_host_packets(self, downstream_connection_bind_slot: BindSlot):
        pass

    async def _run(self):
        tasks = [create_task(self._process_host_to_target_packets())]
        for downstream_connection_bind_slot in self._downstream_connections:
            task = create_task(
                self._process_target_to_host_packets(downstream_connection_bind_slot)
            )
            tasks.append(task)
        await self._change_status_to_running()
        await gather(*tasks)

    async def _stop(self):
        await self._upstream_connection_fifo.host_to_target.put(None)
        for downstream_connection_fifo in self._downstream_connection_fifos:
            await downstream_connection_fifo.target_to_host.put(None)


class CxlIoRouter(RunnableComponent):
    def __init__(
        self,
        vcs_id: int,
        routing_table: RoutingTable,
        usp_device: UpstreamPortDevice,
        port_binder: PortBinder,
    ):
        super().__init__()
        self._config_space_router = ConfigSpaceRouter(
            vcs_id, routing_table, usp_device, port_binder
        )
        self._mmio_router = MmioRouter(vcs_id, routing_table, usp_device, port_binder)

    async def _run(self):
        run_tasks = [
            create_task(self._config_space_router.run()),
            create_task(self._mmio_router.run()),
        ]
        wait_tasks = [
            create_task(self._config_space_router.wait_for_ready()),
            create_task(self._mmio_router.wait_for_ready()),
        ]
        await gather(*wait_tasks)
        await self._change_status_to_running()
        await gather(*run_tasks)

    async def _stop(self):
        stop_tasks = [
            create_task(self._config_space_router.stop()),
            create_task(self._mmio_router.stop()),
        ]
        await gather(*stop_tasks)


class MmioRouter(CxlRouter):
    def __init__(
        self,
        vcs_id: int,
        routing_table: RoutingTable,
        usp_device: UpstreamPortDevice,
        port_binder: PortBinder,
    ):
        usp_connection = usp_device.get_downstream_connection()

        super().__init__(vcs_id, routing_table)
        self._upstream_connection_fifo = usp_connection.mmio_fifo
        self._downstream_connections = port_binder.get_bind_slots()
        self._downstream_connection_fifos = [
            self._downstream_connections[i].vppb_connection.mmio_fifo
            for i in range(len(self._downstream_connections))
        ]

    async def _process_host_to_target_packets(self):
        while True:
            packet = await self._upstream_connection_fifo.host_to_target.get()
            if packet is None:
                break

            logger.debug(self._create_message("Received an incoming request"))
            base_packet = cast(BasePacket, packet)
            cxl_io_base_packet = cast(CxlIoBasePacket, packet)
            if not (base_packet.is_cxl_io() and cxl_io_base_packet.is_mmio()):
                raise Exception(f"Received unexpected packet: {base_packet.get_type()}")

            mmio_packet = cast(CxlIoMemReqPacket, packet)
            address = mmio_packet.get_address()
            size = mmio_packet.get_data_size()
            req_id = tlptoh16(mmio_packet.mreq_header.req_id)
            tag = mmio_packet.mreq_header.tag
            target_port = self._routing_table.get_mmio_target_port(address)
            if target_port is None:
                if mmio_packet.is_mem_read():
                    logger.debug(self._create_message(f"RD: 0x{address:x}[{size}] OOB"))
                    await self._send_completion(req_id, tag, data=0, data_len=size)
                elif mmio_packet.is_mem_write():
                    logger.debug(self._create_message(f"WR: 0x{address:x}[{size}] OOB"))
                continue

            if target_port >= len(self._downstream_connections):
                raise Exception("target_port is out of bound")

            downstream_connection_fifo = self._downstream_connections[
                target_port
            ].vppb_connection.mmio_fifo
            await downstream_connection_fifo.host_to_target.put(packet)

    async def _process_target_to_host_packets(self, downstream_connection_bind_slot: BindSlot):
        downstream_connection_fifo = downstream_connection_bind_slot.vppb_connection.mmio_fifo
        while True:
            packet = await downstream_connection_fifo.target_to_host.get()
            if packet is None:
                break
            await self._upstream_connection_fifo.target_to_host.put(packet)

    async def _send_completion(self, req_id, tag, data: int = None, data_len: int = 0):
        """
        Note that data_len should be in bytes.
        """
        if data is not None:
            packet = CxlIoCompletionWithDataPacket.create(req_id, tag, data, pload_len=data_len)
        else:
            packet = CxlIoCompletionPacket.create(req_id, tag)
        await self._upstream_connection_fifo.target_to_host.put(packet)


class ConfigSpaceRouter(CxlRouter):
    def __init__(
        self,
        vcs_id: int,
        routing_table: RoutingTable,
        usp_device: UpstreamPortDevice,
        port_binder: PortBinder,
    ):
        usp_connection = usp_device.get_downstream_connection()
        super().__init__(vcs_id, routing_table)
        self._upstream_connection_fifo = usp_connection.cfg_fifo
        self._downstream_connections = port_binder.get_bind_slots()
        self._downstream_connection_fifos = [
            self._downstream_connections[i].vppb_connection.cfg_fifo
            for i in range(len(self._downstream_connections))
        ]

    async def _process_host_to_target_packets(self):
        while True:
            packet = await self._upstream_connection_fifo.host_to_target.get()
            if packet is None:
                break

            logger.debug(self._create_message("Received an incoming request"))
            base_packet = cast(BasePacket, packet)
            if not base_packet.is_cxl_io():
                raise Exception(f"Received unexpected packet: {base_packet.get_type()}")

            cxl_io_packet = cast(CxlIoBasePacket, packet)
            if cxl_io_packet.is_cfg_read():
                cfg_packet = cast(CxlIoCfgRdPacket, packet)
            elif cxl_io_packet.is_cfg_write():
                cfg_packet = cast(CxlIoCfgWrPacket, packet)
            else:
                raise Exception(f"Received unexpected packet: {base_packet.get_type()}")
            dest_id = tlptoh16(cfg_packet.cfg_req_header.dest_id)

            logger.debug(self._create_message(f"Destination ID is {bdf_to_string(dest_id)}"))

            req_id = tlptoh16(cfg_packet.cfg_req_header.req_id)
            tag = cfg_packet.cfg_req_header.tag
            target_port = self._routing_table.get_config_space_target_port(dest_id)
            if target_port is None:
                logger.debug(
                    self._create_message(f"Request to {bdf_to_string(dest_id)} is not routable")
                )
                await self._send_unsupported_request(req_id, tag)
                continue
            if target_port >= len(self._downstream_connections):
                logger.warning(self._create_message("target_port is out of bound"))
                await self._send_unsupported_request(req_id, tag)
                continue

            logger.debug(self._create_message(f"Target port is {target_port}"))

            downstream_connection_fifo = self._downstream_connections[
                target_port
            ].vppb_connection.cfg_fifo
            await downstream_connection_fifo.host_to_target.put(packet)

    async def _process_target_to_host_packets(self, downstream_connection_bind_slot: BindSlot):
        downstream_connection_fifo = downstream_connection_bind_slot.vppb_connection.cfg_fifo
        while True:
            packet = await downstream_connection_fifo.target_to_host.get()
            if packet is None:
                break
            await self._upstream_connection_fifo.target_to_host.put(packet)

    async def _send_unsupported_request(self, req_id, tag):
        packet = CxlIoCompletionPacket.create(req_id, tag, CXL_IO_CPL_STATUS.UR)
        await self._upstream_connection_fifo.target_to_host.put(packet)


class CxlMemRouter(CxlRouter):
    def __init__(
        self,
        vcs_id: int,
        routing_table: RoutingTable,
        usp_device: UpstreamPortDevice,
        port_binder: PortBinder,
        bi_enable_override_for_test: Optional[int] = None,
        bi_forward_override_for_test: Optional[int] = None,
    ):
        usp_connection = usp_device.get_downstream_connection()
        self._usp_device = usp_device
        self._port_binder = port_binder

        # For testing purposes
        self._bi_enable_override_for_test = bi_enable_override_for_test
        self._bi_forward_override_for_test = bi_forward_override_for_test

        super().__init__(vcs_id, routing_table)
        self._upstream_connection_fifo = usp_connection.cxl_mem_fifo
        self._downstream_connections = port_binder.get_bind_slots()
        self._downstream_connection_fifos = [
            self._downstream_connections[i].vppb_connection.cxl_mem_fifo
            for i in range(len(self._downstream_connections))
        ]

    async def _process_host_to_target_packets(self):
        while True:
            packet = await self._upstream_connection_fifo.host_to_target.get()
            if packet is None:
                break

            target_port = None

            cxl_mem_base_packet = cast(CxlMemBasePacket, packet)
            if cxl_mem_base_packet.is_m2sreq():
                cxl_mem_packet = cast(CxlMemM2SReqPacket, packet)
                addr = cxl_mem_packet.get_address()
                target_port = self._routing_table.get_cxl_mem_target_port(addr)
            elif cxl_mem_base_packet.is_m2srwd():
                cxl_mem_packet = cast(CxlMemM2SRwDPacket, packet)
                addr = cxl_mem_packet.get_address()
                target_port = self._routing_table.get_cxl_mem_target_port(addr)
            elif cxl_mem_base_packet.is_m2sbirsp():
                cxl_mem_bi_packet: CxlMemM2SBIRspPacket = cast(
                    CxlMemM2SBIRspPacket, cxl_mem_base_packet
                )
                for i, bind_slot in enumerate(self._downstream_connections):
                    dsp_device = bind_slot.dsp
                    bus = dsp_device.get_secondary_bus_number()
                    if bus == cxl_mem_bi_packet.m2sbirsp_header.bi_id:
                        target_port = i
                        break
            else:
                raise Exception("Received unexpected packet")

            if target_port is None:
                logger.warning(self._create_message("Received unroutable CXL.mem packet"))
                continue
            if target_port >= len(self._downstream_connections):
                raise Exception("target_port is out of bound")
            downstream_connection_fifo = self._downstream_connections[
                target_port
            ].vppb_connection.cxl_mem_fifo
            await downstream_connection_fifo.host_to_target.put(packet)

    async def _process_target_to_host_packets(self, downstream_connection_bind_slot: BindSlot):
        downstream_connection_fifo = downstream_connection_bind_slot.vppb_connection.cxl_mem_fifo
        dsp_device = downstream_connection_bind_slot.dsp

        dsp_component = dsp_device.get_cxl_component()
        usp_component = self._usp_device.get_cxl_component()
        bi_enable = self._bi_enable_override_for_test
        bi_forward = self._bi_forward_override_for_test

        while True:
            packet = await downstream_connection_fifo.target_to_host.get()
            if packet is None:
                break
            cxl_mem_base_packet: CxlMemBasePacket = cast(CxlMemBasePacket, packet)
            if cxl_mem_base_packet.is_s2mbisnp():
                # NOTE: Following vars might be uninitialized before while
                bi_id = dsp_device.get_secondary_bus_number()
                bi_decoder_options = dsp_component.get_bi_decoder_options()

                if self._bi_enable_override_for_test is None:
                    bi_enable = bi_decoder_options["control_options"]["bi_enable"]
                if self._bi_forward_override_for_test is None:
                    bi_forward = bi_decoder_options["control_options"]["bi_forward"]

                cxl_mem_bi_packet: CxlMemS2MBISnpPacket = cast(
                    CxlMemS2MBISnpPacket, cxl_mem_base_packet
                )
                if bi_enable == bi_forward:
                    continue

                if bi_enable == 0 and bi_forward == 1:
                    await self._upstream_connection_fifo.target_to_host.put(packet)
                elif bi_enable == 1 and bi_forward == 0:
                    hdm_decoder_manager = usp_component.get_hdm_decoder_manager()
                    if hdm_decoder_manager.is_bi_capable():
                        cxl_mem_bi_packet.s2mbisnp_header.bi_id = bi_id
                        await self._upstream_connection_fifo.target_to_host.put(packet)
                    else:
                        continue
            else:
                await self._upstream_connection_fifo.target_to_host.put(packet)


class CxlCacheRouter(CxlRouter):
    def __init__(
        self,
        vcs_id: int,
        routing_table: RoutingTable,
        usp_device: UpstreamPortDevice,
        vppb_connections: List[CxlConnection],
    ):
        super().__init__(vcs_id, routing_table)
        self._usp_device = usp_device
        usp_connection = self._usp_device.get_downstream_connection()
        self._upstream_connection = usp_connection.cxl_cache_fifo
        self._downstream_connections = [
            vppb_connection.cxl_cache_fifo for vppb_connection in vppb_connections
        ]

    async def _process_host_to_target_packets(self):
        while True:
            packet = await self._upstream_connection_fifo.host_to_target.get()
            if packet is None:
                break

            cxl_cache_base_packet = cast(CxlCacheBasePacket, packet)
            if cxl_cache_base_packet.is_h2dreq():
                cxl_cache_packet = cast(CxlCacheH2DReqPacket, packet)
                cache_id = cxl_cache_packet.h2dreq_header.cache_id
            elif cxl_cache_base_packet.is_h2drsp():
                cxl_cache_packet = cast(CxlCacheH2DRspPacket, packet)
                cache_id = cxl_cache_packet.h2drsp_header.cache_id
            else:
                raise Exception("Received unexpected packet")

            usp_component = self._usp_device.get_cxl_component()

            # HACK: this is a placeholder that only works with structures having a
            # fixed number of targets. A MUCH better way of doing this is through an
            # indexed memory read, but in the interest of time, and due to the
            # unstability of UnalignedBitStructure, that solution is probably unwise.

            target_fld_name = f"target{cache_id}_options"

            if target_fld_name not in usp_component.get_cache_route_table_options:
                logger.warning(self._create_message("Received unroutable CXL.cache packet"))
                continue
            target_port = usp_component.get_cache_route_table_options[target_fld_name][
                "port_number"
            ]

            if target_port >= len(self._downstream_connections):
                raise Exception("target_port is out of bound")

            downstream_connection = self._downstream_connections[
                target_port
            ].vppb_connection.cxl_cache_fifo
            await downstream_connection.host_to_target.put(packet)

    async def _process_target_to_host_packets(self, downstream_connection_bind_slot: BindSlot):
        downstream_connection_fifo = downstream_connection_bind_slot.vppb_connection.cxl_cache_fifo

        dsp_device = downstream_connection_bind_slot.dsp
        dsp_component = dsp_device.get_cxl_component()

        while True:
            packet = await downstream_connection_fifo.target_to_host.get()
            if packet is None:
                break
            cxl_cache_base_packet = cast(CxlCacheBasePacket, packet)

            # See CXL 3.0 specification: Section 9.15.2
            if cxl_cache_base_packet.is_d2hreq():
                cxl_cache_packet = cast(CxlCacheD2HReqPacket, packet)
                cache_id_decoder_opt_ctl = dsp_component.get_cache_decoder_options()[
                    "control_options"
                ]
                assign, fwd = (
                    cache_id_decoder_opt_ctl["assign_cache_id"],
                    cache_id_decoder_opt_ctl["forward_cache_id"],
                )
                if (assign, fwd) == (1, 1):
                    logger.error(self._create_message("Invalid setting: assign/fwd cannot be 1/1"))
                elif (assign, fwd) == (0, 1):
                    pass  # just forward upstream
                elif (assign, fwd) == (1, 0):
                    # get the local cache id
                    cache_id = cache_id_decoder_opt_ctl["local_cache_id"]
                    cxl_cache_packet.set_cache_id(cache_id)
            await self._upstream_connection_fifo.target_to_host.put(packet)
