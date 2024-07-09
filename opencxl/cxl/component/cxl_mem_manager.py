"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from typing import Optional, cast

from opencxl.util.logger import logger
from opencxl.pci.component.fifo_pair import FifoPair
from opencxl.cxl.transport.transaction import (
    BasePacket,
    CxlMemBasePacket,
    CxlMemM2SBiRspPacket,
    CxlMemM2SReqPacket,
    CxlMemM2SRwDPacket,
    CxlMemMemBiRspPacket,
    CxlMemMemBiSnpPacket,
    CxlMemMemRdPacket,
    CxlMemMemWrPacket,
    CxlMemMemDataPacket,
    CxlMemCmpPacket,
)
from opencxl.cxl.component.cxl_memory_device_component import CxlMemoryDeviceComponent
from opencxl.pci.component.packet_processor import PacketProcessor


class CxlMemManager(PacketProcessor):
    def __init__(
        self,
        upstream_fifo: FifoPair,
        downstream_fifo: Optional[FifoPair] = None,
        label: Optional[str] = None,
    ):
        self._downstream_fifo: Optional[FifoPair]
        self._upstream_fifo: FifoPair

        super().__init__(upstream_fifo, downstream_fifo, label)
        self._memory_device_component: Optional[CxlMemoryDeviceComponent] = None

    def set_memory_device_component(self, memory_device_component: CxlMemoryDeviceComponent):
        self._memory_device_component = memory_device_component

    async def _process_cxl_mem_rd_packet(self, mem_rd_packet: CxlMemMemRdPacket):
        if self._downstream_fifo is not None:
            logger.debug(self._create_message("Forwarding CXL.mem MEM_RD packet"))
            await self._downstream_fifo.host_to_target.put(mem_rd_packet)
            return

        if self._memory_device_component is None:
            raise Exception("CxlMemoryDeviceComponent isn't set yet")

        address = mem_rd_packet.get_address()
        data = await self._memory_device_component.read_mem(address)

        packet = CxlMemMemDataPacket.create(data)
        await self._upstream_fifo.target_to_host.put(packet)

    async def _process_cxl_mem_wr_packet(self, mem_wr_packet: CxlMemMemWrPacket):
        if self._downstream_fifo is not None:
            logger.debug(self._create_message("Forwarding CXL.mem MEM_WR packet"))
            await self._downstream_fifo.host_to_target.put(mem_wr_packet)
            return

        if self._memory_device_component is None:
            raise Exception("CxlMemoryDeviceComponent isn't set yet")

        address = mem_wr_packet.get_address()
        data = mem_wr_packet.data
        await self._memory_device_component.write_mem(address, data)

        packet = CxlMemCmpPacket.create()
        await self._upstream_fifo.target_to_host.put(packet)

    async def process_cxl_mem_bisnp_packet(self, mem_bisnp_packet: CxlMemMemBiSnpPacket):
        await self._process_cxl_mem_bisnp_packet(mem_bisnp_packet)

    async def _process_cxl_mem_bisnp_packet(self, mem_bisnp_packet: CxlMemMemBiSnpPacket):
        if self._upstream_fifo is not None:
            print(self._create_message("Forwarding CXL.mem MEM_BISNP packet"))
            await self._upstream_fifo.target_to_host.put(mem_bisnp_packet)
            return

    async def _process_cxl_mem_birsp_packet(self, mem_birsp_packet: CxlMemMemBiRspPacket):
        if self._downstream_fifo is not None:
            logger.debug(self._create_message("Forwarding CXL.mem MEM_BIRSP packet"))
            await self._downstream_fifo.host_to_target.put(mem_birsp_packet)
            return
        # TODO: add logics for handling BIRsp packets
        logger.debug(self._create_message("Reached _process_cxl_mem_birsp_packet"))
        return

    async def _process_host_to_target(self):
        logger.debug(self._create_message("Started processing incoming fifo"))
        while True:
            packet = await self._upstream_fifo.host_to_target.get()
            if packet is None:
                logger.debug(self._create_message("Stopped processing incoming fifo"))
                break

            base_packet = cast(BasePacket, packet)
            if not base_packet.is_cxl_mem():
                raise Exception(f"Received unexpected packet: {base_packet.get_type()}")

            logger.debug(self._create_message("Received incoming packet"))
            cxl_mem_packet = cast(CxlMemBasePacket, packet)

            if cxl_mem_packet.is_m2sreq():
                m2sreq_packet = cast(CxlMemM2SReqPacket, packet)
                if m2sreq_packet.is_mem_rd():
                    await self._process_cxl_mem_rd_packet(cast(CxlMemMemRdPacket, m2sreq_packet))
                else:
                    raise Exception(
                        f"Unsupported MEM Opcode: {m2sreq_packet.m2sreq_header.mem_opcode}"
                    )
            elif cxl_mem_packet.is_m2srwd():
                m2srwd_packet = cast(CxlMemM2SRwDPacket, packet)
                if m2srwd_packet.is_mem_wr():
                    await self._process_cxl_mem_wr_packet(cast(CxlMemMemWrPacket, m2srwd_packet))
                else:
                    raise Exception(
                        f"Unsupported MEM Opcode: {m2srwd_packet.m2srwd_header.mem_opcode}"
                    )
            elif cxl_mem_packet.is_m2sbirsp():
                m2sbirsp_packet = cast(CxlMemM2SBiRspPacket, packet)
                if m2sbirsp_packet.is_m2sbirsp():
                    await self._process_cxl_mem_birsp_packet(
                        cast(CxlMemMemBiRspPacket, m2sbirsp_packet)
                    )
                else:
                    raise Exception(
                        f"Unsupported BIRsp packet, tag: {m2sbirsp_packet.m2sbirsp_header.bi_tag}"
                    )
            else:
                raise Exception(f"Received unexpected packet: {base_packet.get_type()}")
