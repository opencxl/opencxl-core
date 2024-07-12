"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

# pylint: disable=unused-import
from typing import Optional, cast

from opencxl.cxl.component.cxl_memory_device_component import CxlMemoryDeviceComponent
from opencxl.util.logger import logger
from opencxl.pci.component.fifo_pair import FifoPair
from opencxl.cxl.transport.transaction import (
    BasePacket,
    CxlCacheBasePacket,
    CxlCacheH2DReqPacket,
    CxlCacheH2DRspPacket,
    CxlCacheH2DDataPacket,
    CxlCacheCacheD2HReqPacket,
    CxlCacheCacheD2HRspPacket,
    CxlCacheCacheD2HDataPacket,
    CxlCacheCacheH2DReqPacket,
    CxlCacheCacheH2DRspPacket,
    CxlCacheCacheH2DDataPacket,
    CXL_CACHE_H2DREQ_OPCODE,
    CXL_CACHE_H2DRSP_OPCODE,
    CXL_CACHE_D2HREQ_OPCODE,
    CXL_CACHE_D2HRSP_OPCODE,
)
from opencxl.pci.component.packet_processor import PacketProcessor


class CxlCacheManager(PacketProcessor):
    def __init__(
        self,
        upstream_fifo: FifoPair,
        downstream_fifo: Optional[FifoPair] = None,
        label: Optional[str] = None,
    ):
        self._downstream_fifo: Optional[FifoPair]
        self._upstream_fifo: FifoPair
        self._cache_device_component = None

        super().__init__(upstream_fifo, downstream_fifo, label)

    def set_memory_device_component(self, cache_device_component: CxlMemoryDeviceComponent):
        self._cache_device_component = cache_device_component

    async def send_d2h_req_test(self):
        # Test func 1: Sending D2H Req
        # Addr must be aligned to 0x40
        # cache_id is 4-bit
        # Some arbitrary opcode
        packet = CxlCacheCacheD2HReqPacket.create(
            addr=0x40, cache_id=0b1010, opcode=CXL_CACHE_D2HREQ_OPCODE.CACHE_RD_ANY
        )
        await self._upstream_fifo.target_to_host.put(packet)

    async def send_d2h_rsp_test(self):
        # Test func 2: Sending D2H Rsp
        # uqid is 12-bit
        # Some arbitrary opcode
        packet = CxlCacheCacheD2HRspPacket.create(
            uqid=0b111100001010, opcode=CXL_CACHE_D2HRSP_OPCODE.RSP_I_FWD_M
        )
        await self._upstream_fifo.target_to_host.put(packet)

    async def send_d2h_data_test(self):
        # Test func 3: Sending D2H Data
        # uqid is 12-bit
        # Data is 64-byte
        # Create 64-byte data: 0x0000000011111111........FFFFFFFF
        data = 0x00000000
        for i in range(16):
            data <<= 32
            data |= int(str(f"{i:x}") * 8, 16)
        packet = CxlCacheCacheD2HDataPacket.create(
            uqid=0b111100001010,
            data=data,
        )
        await self._upstream_fifo.target_to_host.put(packet)

    async def _process_host_to_target(self):
        logger.debug(self._create_message("Started processing incoming CXL.cache fifo"))
        while True:
            packet = await self._upstream_fifo.host_to_target.get()
            if packet is None:
                logger.debug(self._create_message("Stopped processing incoming CXL.cache fifo"))
                break

            base_packet = cast(BasePacket, packet)
            if not base_packet.is_cxl_cache():
                raise Exception(f"Received unexpected CXL.cache packet: {base_packet.get_type()}")

            logger.debug(self._create_message("Received incoming CXL.cache packet"))
            cxl_cache_packet = cast(CxlCacheBasePacket, packet)

            if cxl_cache_packet.is_h2dreq():
                h2dreq_packet = cast(CxlCacheCacheH2DReqPacket, packet)
                print("Received h2dreq_packet:")
                h2dreq_packet.get_pretty_string()
            elif cxl_cache_packet.is_h2drsp():
                h2drsp_packet = cast(CxlCacheCacheH2DRspPacket, packet)
                print("Received h2drsp_packet:")
                h2drsp_packet.get_pretty_string()
            elif cxl_cache_packet.is_h2ddata():
                h2ddata_packet = cast(CxlCacheCacheH2DDataPacket, packet)
                print("Received h2ddata_packet:")
                h2ddata_packet.get_pretty_string()
            else:
                raise Exception(f"Received unexpected packet: {base_packet.get_type()}")
