"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from dataclasses import dataclass
from asyncio import create_task, gather
from typing import cast

from opencxl.util.logger import logger
from opencxl.util.component import RunnableComponent
from opencxl.pci.component.fifo_pair import FifoPair
from opencxl.cxl.transport.memory_fifo import (
    MemoryFifoPair,
    MemoryRequest,
    MEMORY_REQUEST_TYPE,
)
from opencxl.cxl.transport.cache_fifo import (
    CacheFifoPair,
    CacheRequest,
    CACHE_REQUEST_TYPE,
    CacheResponse,
    CACHE_RESPONSE_STATUS,
)
from opencxl.cxl.transport.transaction import (
    CxlCacheBasePacket,
    CxlCacheD2HReqPacket,
    CxlCacheD2HRspPacket,
    CxlCacheD2HDataPacket,
    CxlCacheCacheH2DReqPacket,
    CxlCacheCacheH2DRspPacket,
    CxlCacheCacheD2HDataPacket,
    CXL_CACHE_H2DREQ_OPCODE,
    CXL_CACHE_H2DRSP_OPCODE,
    CXL_CACHE_H2DRSP_CACHE_STATE,
    CXL_CACHE_D2HREQ_OPCODE,
    CXL_CACHE_D2HRSP_OPCODE,
)


@dataclass
class CacheCoherencyBridgeConfig:
    host_name: str
    memory_producer_fifos: MemoryFifoPair
    upstream_cache_to_coh_bridge_fifo: CacheFifoPair
    upstream_coh_bridge_to_cache_fifo: CacheFifoPair
    downstream_cxl_cache_fifos: FifoPair


class CacheCoherencyBridge(RunnableComponent):
    def __init__(self, config: CacheCoherencyBridgeConfig):
        super().__init__(lambda class_name: f"{config.host_name}:{class_name}")

        self._memory_producer_fifos = config.memory_producer_fifos
        self._upstream_cache_to_coh_bridge_fifo = config.upstream_cache_to_coh_bridge_fifo
        self._upstream_coh_bridge_to_cache_fifo = config.upstream_coh_bridge_to_cache_fifo
        self._downstream_cxl_cache_fifos = config.downstream_cxl_cache_fifos

        self._haddr = 0
        self._daddr = 0

    async def _process_upstream_host_to_target_packets(self):
        while True:
            cache_packet = await self._upstream_cache_to_coh_bridge_fifo.request.get()
            if cache_packet is None:
                logger.debug(
                    self._create_message(
                        "Stopped processing upstream host to target CXL.mem packets"
                    )
                )
                break
            addr = cache_packet.address

            if cache_packet.type == CACHE_REQUEST_TYPE.WRITE_BACK:
                mem_packet = MemoryRequest(
                    MEMORY_REQUEST_TYPE.WRITE, addr, cache_packet.size, cache_packet.data
                )
                await self._memory_producer_fifos.request.put(mem_packet)
            else:
                self._haddr = addr
                if cache_packet.type == CACHE_REQUEST_TYPE.SNP_DATA:
                    opcode = CXL_CACHE_H2DREQ_OPCODE.SNP_DATA
                elif cache_packet.type == CACHE_REQUEST_TYPE.SNP_INV:
                    opcode = CXL_CACHE_H2DREQ_OPCODE.SNP_INV
                elif cache_packet.type == CACHE_REQUEST_TYPE.SNP_CUR:
                    opcode = CXL_CACHE_H2DREQ_OPCODE.SNP_CUR
                cxl_packet = CxlCacheCacheH2DReqPacket.create(addr, opcode)
                await self._downstream_cxl_cache_fifos.host_to_target.put(cxl_packet)

    async def _process_downstream_target_to_host_packets(self):
        while True:
            cxl_packet = await self._downstream_cxl_cache_fifos.target_to_host.get()
            if cxl_packet is None:
                logger.debug(
                    self._create_message(
                        "Stopped processing downstream target to host CXL.mem packets"
                    )
                )
                break
            base_packet = cast(CxlCacheBasePacket, cxl_packet)

            if base_packet.is_d2hreq():
                packet = cast(CxlCacheD2HReqPacket, cxl_packet)
                addr = packet.get_address()

                if (
                    packet.d2hreq_header.cache_opcode
                    == CXL_CACHE_D2HREQ_OPCODE.CACHE_RD_OWN_NO_DATA
                ):
                    cache_packet = CacheRequest(CACHE_REQUEST_TYPE.SNP_INV, addr)
                    await self._upstream_coh_bridge_to_cache_fifo.request.put(cache_packet)
                    packet = await self._upstream_coh_bridge_to_cache_fifo.response.get()

                    cxl_packet = CxlCacheCacheH2DRspPacket.create(
                        CXL_CACHE_H2DRSP_OPCODE.GO, CXL_CACHE_H2DRSP_CACHE_STATE.EXCLUSIVE
                    )
                    await self._downstream_cxl_cache_fifos.host_to_target.put(cxl_packet)

                elif packet.d2hreq_header.cache_opcode == CXL_CACHE_D2HREQ_OPCODE.CACHE_DIRTY_EVICT:
                    self._daddr = addr
                    cxl_packet = CxlCacheCacheH2DRspPacket.create(
                        CXL_CACHE_H2DRSP_OPCODE.GO_WRITE_PULL,
                        CXL_CACHE_H2DRSP_CACHE_STATE.INVALID,
                    )
                    await self._downstream_cxl_cache_fifos.host_to_target.put(cxl_packet)

                elif packet.d2hreq_header.cache_opcode == CXL_CACHE_D2HREQ_OPCODE.CACHE_RD_SHARED:
                    cache_packet = CacheRequest(CACHE_REQUEST_TYPE.SNP_DATA, addr)
                    await self._upstream_coh_bridge_to_cache_fifo.request.put(cache_packet)
                    packet = await self._upstream_coh_bridge_to_cache_fifo.response.get()

                    if packet.status == CACHE_RESPONSE_STATUS.RSP_MISS:
                        mem_packet = MemoryRequest(MEMORY_REQUEST_TYPE.READ, addr, 64)
                        await self._memory_producer_fifos.request.put(mem_packet)
                        packet = await self._memory_producer_fifos.response.get()

                    cxl_packet = CxlCacheCacheH2DRspPacket.create(
                        CXL_CACHE_H2DRSP_OPCODE.GO, CXL_CACHE_H2DRSP_CACHE_STATE.SHARED
                    )
                    await self._downstream_cxl_cache_fifos.host_to_target.put(cxl_packet)
                    cxl_packet = CxlCacheCacheD2HDataPacket.create(0, packet.data)
                    await self._downstream_cxl_cache_fifos.host_to_target.put(cxl_packet)

            elif base_packet.is_d2hdata():
                packet = cast(CxlCacheD2HDataPacket, cxl_packet)
                mem_packet = MemoryRequest(MEMORY_REQUEST_TYPE.WRITE, self._daddr, 64, packet.data)
                await self._memory_producer_fifos.request.put(mem_packet)

            elif base_packet.is_d2hrsp():
                packet = cast(CxlCacheD2HRspPacket, cxl_packet)
                if packet.d2hrsp_header.cache_opcode < CXL_CACHE_D2HRSP_OPCODE.RSP_S_FWD_M:
                    if packet.d2hrsp_header.cache_opcode in (
                        CXL_CACHE_D2HRSP_OPCODE.RSP_I_HIT_I,
                        CXL_CACHE_D2HRSP_OPCODE.RSP_I_HIT_SE,
                    ):
                        status = CACHE_RESPONSE_STATUS.RSP_I
                    elif packet.d2hrsp_header.cache_opcode == CXL_CACHE_D2HRSP_OPCODE.RSP_S_HIT_SE:
                        status = CACHE_RESPONSE_STATUS.RSP_S

                    mem_packet = MemoryRequest(MEMORY_REQUEST_TYPE.READ, self._haddr, 64)
                    await self._memory_producer_fifos.request.put(mem_packet)
                    packet = await self._memory_producer_fifos.response.get()
                    cache_packet = CacheResponse(status, packet.data)

                else:
                    if packet.d2hrsp_header.cache_opcode == CXL_CACHE_D2HRSP_OPCODE.RSP_S_FWD_M:
                        status = CACHE_RESPONSE_STATUS.RSP_S
                    elif packet.d2hrsp_header.cache_opcode == CXL_CACHE_D2HRSP_OPCODE.RSP_I_FWD_M:
                        status = CACHE_RESPONSE_STATUS.RSP_I

                    if not self._downstream_cxl_cache_fifos.target_to_host.empty():
                        cxl_packet = await self._downstream_cxl_cache_fifos.target_to_host.get()
                        base_packet = cast(CxlCacheBasePacket, cxl_packet)
                        assert base_packet.is_d2hdata()
                        packet = cast(CxlCacheD2HDataPacket, cxl_packet)
                        cache_packet = CacheResponse(status, packet.data)

                await self._upstream_cache_to_coh_bridge_fifo.response.put(cache_packet)

    async def _run(self):
        tasks = [
            create_task(self._process_upstream_host_to_target_packets()),
            create_task(self._process_downstream_target_to_host_packets()),
        ]
        await self._change_status_to_running()
        await gather(*tasks)

    async def _stop(self):
        await self._upstream_cache_to_coh_bridge_fifo.request.put(None)
        await self._downstream_cxl_cache_fifos.target_to_host.put(None)
