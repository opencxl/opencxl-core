"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from abc import abstractmethod
from asyncio import gather, create_task
from typing import List, cast

from opencxl.util.logger import logger
from opencxl.util.component import RunnableComponent
from opencxl.util.pci import bdf_to_string
from opencxl.util.number import tlptoh16
from opencxl.cxl.component.cxl_connection import CxlConnection, FifoPair
from opencxl.cxl.component.virtual_switch.routing_table import RoutingTable
from opencxl.cxl.transport.transaction import (
    BasePacket,
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
)


class CxlRouter(RunnableComponent):
    def __init__(
        self,
        vcs_id: int,
        routing_table: RoutingTable,
    ):
        self._downstream_connections: List[FifoPair]
        self._upstream_connection: FifoPair

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
    async def _process_target_to_host_packets(self, downstream_connection: FifoPair):
        pass

    async def _run(self):
        tasks = [create_task(self._process_host_to_target_packets())]
        for downstream_connection in self._downstream_connections:
            task = create_task(self._process_target_to_host_packets(downstream_connection))
            tasks.append(task)
        await self._change_status_to_running()
        await gather(*tasks)

    async def _stop(self):
        await self._upstream_connection.host_to_target.put(None)
        for downstream_connection in self._downstream_connections:
            await downstream_connection.target_to_host.put(None)


class CxlIoRouter(RunnableComponent):
    def __init__(
        self,
        vcs_id: int,
        routing_table: RoutingTable,
        usp_connection: CxlConnection,
        vppb_connections: List[CxlConnection],
    ):
        super().__init__()
        self._config_space_router = ConfigSpaceRouter(
            vcs_id, routing_table, usp_connection, vppb_connections
        )
        self._mmio_router = MmioRouter(vcs_id, routing_table, usp_connection, vppb_connections)

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
        usp_connection: CxlConnection,
        vppb_connections: List[CxlConnection],
    ):
        super().__init__(vcs_id, routing_table)
        self._upstream_connection = usp_connection.mmio_fifo
        self._downstream_connections = [
            vppb_connection.mmio_fifo for vppb_connection in vppb_connections
        ]

    async def _process_host_to_target_packets(self):
        while True:
            packet = await self._upstream_connection.host_to_target.get()
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

            # MLD
            cxl_io_base_packet.tlp_prefix.ld_id = target_port

            downstream_connection = self._downstream_connections[target_port]
            await downstream_connection.host_to_target.put(packet)

    async def _process_target_to_host_packets(self, downstream_connection: FifoPair):
        while True:
            packet = await downstream_connection.target_to_host.get()
            if packet is None:
                break
            await self._upstream_connection.target_to_host.put(packet)

    async def _send_completion(self, req_id, tag, data: int = None, data_len: int = 0):
        """
        Note that data_len should be in bytes.
        """
        if data is not None:
            packet = CxlIoCompletionWithDataPacket.create(req_id, tag, data, pload_len=data_len)
        else:
            packet = CxlIoCompletionPacket.create(req_id, tag)
        await self._upstream_connection.target_to_host.put(packet)


class ConfigSpaceRouter(CxlRouter):
    def __init__(
        self,
        vcs_id: int,
        routing_table: RoutingTable,
        usp_connection: CxlConnection,
        vppb_connections: List[CxlConnection],
    ):
        super().__init__(vcs_id, routing_table)
        self._upstream_connection = usp_connection.cfg_fifo
        self._downstream_connections = [
            vppb_connection.cfg_fifo for vppb_connection in vppb_connections
        ]

    async def _process_host_to_target_packets(self):
        while True:
            packet = await self._upstream_connection.host_to_target.get()
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

            # MLD
            cxl_io_packet.tlp_prefix.ld_id = target_port

            logger.debug(self._create_message(f"Target port is {target_port}"))

            downstream_connection = self._downstream_connections[target_port]
            await downstream_connection.host_to_target.put(packet)

    async def _process_target_to_host_packets(self, downstream_connection: FifoPair):
        while True:
            packet = await downstream_connection.target_to_host.get()
            if packet is None:
                break
            await self._upstream_connection.target_to_host.put(packet)

    async def _send_unsupported_request(self, req_id, tag):
        packet = CxlIoCompletionPacket.create(req_id, tag, CXL_IO_CPL_STATUS.UR)
        await self._upstream_connection.target_to_host.put(packet)


class CxlMemRouter(CxlRouter):
    def __init__(
        self,
        vcs_id: int,
        routing_table: RoutingTable,
        usp_connection: CxlConnection,
        vppb_connections: List[CxlConnection],
    ):
        super().__init__(vcs_id, routing_table)
        self._upstream_connection = usp_connection.cxl_mem_fifo
        self._downstream_connections = [
            vppb_connection.cxl_mem_fifo for vppb_connection in vppb_connections
        ]

    async def _process_host_to_target_packets(self):
        while True:
            packet = await self._upstream_connection.host_to_target.get()
            if packet is None:
                break

            cxl_mem_base_packet = cast(CxlMemBasePacket, packet)
            if cxl_mem_base_packet.is_m2sreq():
                cxl_mem_packet = cast(CxlMemM2SReqPacket, packet)
                addr = cxl_mem_packet.get_address()
                target_port = self._routing_table.get_cxl_mem_target_port(addr)
                # MLD
                cxl_mem_packet.m2sreq_header.ld_id = target_port
            elif cxl_mem_base_packet.is_m2srwd():
                cxl_mem_packet = cast(CxlMemM2SRwDPacket, packet)
                addr = cxl_mem_packet.get_address()
                target_port = self._routing_table.get_cxl_mem_target_port(addr)
                # MLD
                cxl_mem_packet.m2srwd_header.ld_id = target_port
            else:
                raise Exception("Received unexpected packet")

            if target_port is None:
                logger.warning(self._create_message("Received unroutable CXL.mem packet"))
                continue
            if target_port >= len(self._downstream_connections):
                raise Exception("target_port is out of bound")

            downstream_connection = self._downstream_connections[target_port]
            await downstream_connection.host_to_target.put(packet)

    async def _process_target_to_host_packets(self, downstream_connection: FifoPair):
        while True:
            packet = await downstream_connection.target_to_host.get()
            if packet is None:
                break
            await self._upstream_connection.target_to_host.put(packet)
