"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from dataclasses import dataclass
from asyncio import create_task, gather
import asyncio
from enum import Enum, auto
from typing import List, cast

from opencxl.util.logger import logger
from opencxl.util.component import RunnableComponent
from opencxl.pci.component.fifo_pair import FifoPair
from opencxl.cxl.transport.memory_fifo import (
    MemoryFifoPair,
    MemoryRequest,
    MEMORY_REQUEST_TYPE,
    MemoryResponse,
    MEMORY_RESPONSE_STATUS,
)
from opencxl.cxl.transport.cache_fifo import (
    CacheFifoPair,
    CacheRequest,
    CACHE_REQUEST_TYPE,
    CacheResponse,
    CACHE_RESPONSE_STATUS,
)
from opencxl.cxl.transport.transaction import (
    CxlMemBasePacket,
    CxlMemMemDataPacket,
    CxlMemMemRdPacket,
    CxlMemMemWrPacket,
    CxlMemBIRspPacket,
    CxlMemS2MNDRPacket,
    CxlMemS2MDRSPacket,
    CxlMemS2MBISnpPacket,
    CXL_MEM_M2SREQ_OPCODE,
    CXL_MEM_M2SRWD_OPCODE,
    CXL_MEM_M2SBIRSP_OPCODE,
    CXL_MEM_S2MNDR_OPCODE,
    CXL_MEM_S2MBISNP_OPCODE,
    CXL_MEM_META_FIELD,
    CXL_MEM_META_VALUE,
    CXL_MEM_M2S_SNP_TYPE,
    is_cxl_mem_data,
)


class MEMORY_RANGE_TYPE(Enum):
    DRAM = auto()
    CXL = auto()
    OOB = auto()


@dataclass
class MemoryRange:
    type: MEMORY_RANGE_TYPE
    base_address: int
    size: int


@dataclass
class HomeAgentConfig:
    host_name: str
    memory_ranges: List[MemoryRange]
    memory_consumer_io_fifos: MemoryFifoPair
    memory_consumer_coh_fifos: MemoryFifoPair
    memory_producer_fifos: MemoryFifoPair
    upstream_cache_to_home_agent_fifo: CacheFifoPair
    upstream_home_agent_to_cache_fifo: CacheFifoPair
    downstream_cxl_mem_fifos: FifoPair


class HomeAgent(RunnableComponent):
    def __init__(self, config: HomeAgentConfig):
        super().__init__(lambda class_name: f"{config.host_name}:{class_name}")

        self._memory_ranges = config.memory_ranges
        self._memory_consumer_io_fifos = config.memory_consumer_io_fifos
        self._memory_consumer_coh_fifos = config.memory_consumer_coh_fifos
        self._memory_producer_fifos = config.memory_producer_fifos
        self._upstream_cache_to_home_agent_fifos = config.upstream_cache_to_home_agent_fifo
        self._upstream_home_agent_to_cache_fifos = config.upstream_home_agent_to_cache_fifo
        self._downstream_cxl_mem_fifos = config.downstream_cxl_mem_fifos

    def _create_m2s_req_packet(
        self,
        opcode: CXL_MEM_M2SREQ_OPCODE,
        meta_field: CXL_MEM_META_FIELD,
        meta_value: CXL_MEM_META_VALUE,
        snp_type: CXL_MEM_M2S_SNP_TYPE,
        addr: int,
    ) -> CxlMemMemRdPacket:
        return CxlMemMemRdPacket.create(addr, opcode, meta_field, meta_value, snp_type)

    def _create_m2s_rwd_packet(
        self,
        opcode: CXL_MEM_M2SRWD_OPCODE,
        meta_field: CXL_MEM_META_FIELD,
        meta_value: CXL_MEM_META_VALUE,
        snp_type: CXL_MEM_M2S_SNP_TYPE,
        addr: int,
        data: int,
    ) -> CxlMemMemWrPacket:
        return CxlMemMemWrPacket.create(addr, data, opcode, meta_field, meta_value, snp_type)

    async def _get_memory_range(self, address: int, size: int) -> MemoryRange:
        for memory_range in self._memory_ranges:
            memory_range_end = memory_range.base_address + memory_range.size - 1
            end_address = address + size - 1
            if address >= memory_range.base_address and end_address <= memory_range_end:
                return memory_range
        return MemoryRange(type=MEMORY_RANGE_TYPE.OOB, base_address=0, size=0)

    async def _write_memory(self, address: int, size: int, value: int):
        packet = MemoryRequest(MEMORY_REQUEST_TYPE.WRITE, address, size, value)
        await self._memory_producer_fifos.request.put(packet)
        packet = await self._memory_producer_fifos.response.get()

        assert packet.status == MEMORY_RESPONSE_STATUS.OK

    async def _read_memory(self, address: int, size: int) -> int:
        packet = MemoryRequest(MEMORY_REQUEST_TYPE.READ, address, size)
        await self._memory_producer_fifos.request.put(packet)
        packet = await self._memory_producer_fifos.response.get()

        return packet.data

    async def write_cxl_mem(self, address: int, size: int, value: int):
        chunk_count = 0
        if size % 8 != 0:
            value <<= 8 * (8 - (size % 8))
        while size > 0:
            message = self._create_message(f"CXL.mem: Writing 0x{value:08x} to 0x{address:08x}")
            logger.debug(message)
            low_8_byte = value & 0xFFFFFFFFFFFFFFFF
            packet = CxlMemMemWrPacket.create(address + (chunk_count * 8), low_8_byte)
            await self._downstream_cxl_mem_fifos.host_to_target.put(packet)
            size -= 8
            chunk_count += 1
            value >>= 64

    async def read_cxl_mem(self, address: int, size: int) -> int:
        diff = 0
        if size % 8 != 0:
            diff = 8 - (size % 8)
            size = size // 8 + 8
        result = 0
        while size > 0:
            message = self._create_message(f"CXL.mem: Reading data from 0x{address:08x}")
            logger.debug(message)
            packet = CxlMemMemRdPacket.create(address + (size - 8))
            await self._downstream_cxl_mem_fifos.host_to_target.put(packet)

            try:
                async with asyncio.timeout(3):
                    packet = await self._downstream_cxl_mem_fifos.target_to_host.get()
                assert is_cxl_mem_data(packet)
                mem_data_packet = cast(CxlMemMemDataPacket, packet)
                size -= 8
                result |= mem_data_packet.data
                result <<= 64
            except asyncio.exceptions.TimeoutError:
                logger.error(self._create_message("CXL.mem Read: Timed-out"))
                return None

        return result >> (diff * 8)

    async def _process_memory_io_bridge_requests(self):
        while True:
            packet = await self._memory_consumer_io_fifos.request.get()
            if packet is None:
                logger.debug(
                    self._create_message("Stopped processing memory access requests from IO Bridge")
                )
                break
            if packet.type == MEMORY_REQUEST_TYPE.WRITE:
                await self._write_memory(packet.address, packet.size, packet.data)
            elif packet.type == MEMORY_REQUEST_TYPE.READ:
                data = await self._read_memory(packet.address, packet.size)
                response = MemoryResponse(MEMORY_RESPONSE_STATUS.OK, data)
                await self._memory_consumer_io_fifos.response.put(response)

    async def _process_memory_coh_bridge_requests(self):
        while True:
            packet = await self._memory_consumer_coh_fifos.request.get()
            if packet is None:
                logger.debug(
                    self._create_message(
                        "Stopped processing memory access requests from Cache Coherency Bridge"
                    )
                )
                break
            if packet.type == MEMORY_REQUEST_TYPE.WRITE:
                await self._write_memory(packet.address, packet.size, packet.data)
            elif packet.type == MEMORY_REQUEST_TYPE.READ:
                data = await self._read_memory(packet.address, packet.size)
                response = MemoryResponse(MEMORY_RESPONSE_STATUS.OK, data)
                await self._memory_consumer_coh_fifos.response.put(response)

    async def _process_upstream_host_to_target_packets(self):
        while True:
            cache_packet = await self._upstream_cache_to_home_agent_fifos.request.get()
            if cache_packet is None:
                logger.debug(
                    self._create_message(
                        "Stopped processing memory access requests from upstream packets"
                    )
                )
                break
            meta_field = CXL_MEM_META_FIELD.NO_OP
            meta_value = CXL_MEM_META_VALUE.INVALID
            snp_type = CXL_MEM_M2S_SNP_TYPE.NO_OP
            addr = cache_packet.address

            if cache_packet.type in (CACHE_REQUEST_TYPE.WRITE, CACHE_REQUEST_TYPE.WRITE_BACK):
                opcode = CXL_MEM_M2SRWD_OPCODE.MEM_WR
                data = cache_packet.data

                # HDM-H Normal Write
                if cache_packet.type == CACHE_REQUEST_TYPE.WRITE:
                    meta_value = CXL_MEM_META_VALUE.ANY
                # HDM-DB Flush Write (Cmp: I/I)
                elif cache_packet.type == CACHE_REQUEST_TYPE.WRITE_BACK:
                    meta_field = CXL_MEM_META_FIELD.META0_STATE
                    meta_value = CXL_MEM_META_VALUE.INVALID

                cxl_packet = self._create_m2s_rwd_packet(
                    opcode, meta_field, meta_value, snp_type, addr, data
                )
            else:
                # HDM-H Normal Read
                if cache_packet.type == CACHE_REQUEST_TYPE.READ:
                    opcode = CXL_MEM_M2SREQ_OPCODE.MEM_RD
                    meta_value = CXL_MEM_META_VALUE.ANY
                # HDM-DB Device Shared Read (Cmp-S: S/S, Cmp-E: A/I)
                elif cache_packet.type == CACHE_REQUEST_TYPE.SNP_DATA:
                    opcode = CXL_MEM_M2SREQ_OPCODE.MEM_RD
                    meta_field = CXL_MEM_META_FIELD.META0_STATE
                    meta_value = CXL_MEM_META_VALUE.SHARED
                    snp_type = CXL_MEM_M2S_SNP_TYPE.SNP_DATA
                # HDM-DB Non-Data, Host Ownership Device Invalidation (Cmp-E: A/I)
                elif cache_packet.type == CACHE_REQUEST_TYPE.SNP_INV:
                    opcode = CXL_MEM_M2SREQ_OPCODE.MEM_INV
                    meta_field = CXL_MEM_META_FIELD.META0_STATE
                    meta_value = CXL_MEM_META_VALUE.ANY
                    snp_type = CXL_MEM_M2S_SNP_TYPE.SNP_INV
                # HDM-DB Non-Cacheable Read, Leaving Device Cache (Cmp: I/A)
                elif cache_packet.type == CACHE_REQUEST_TYPE.SNP_CUR:
                    opcode = CXL_MEM_M2SREQ_OPCODE.MEM_RD
                    meta_field = CXL_MEM_META_FIELD.META0_STATE
                    snp_type = CXL_MEM_M2S_SNP_TYPE.SNP_CUR
                else:
                    logger.debug(self._create_message("Invalid M2S RwD Command"))
                    break

                cxl_packet = self._create_m2s_req_packet(
                    opcode, meta_field, meta_value, snp_type, addr
                )
            await self._downstream_cxl_mem_fifos.host_to_target.put(cxl_packet)

    async def _process_downstream_target_to_host_packets(self):
        while True:
            cxl_packet = await self._downstream_cxl_mem_fifos.target_to_host.get()
            if cxl_packet is None:
                logger.debug(
                    self._create_message(
                        "Stopped processing memory access requests from downstream packets"
                    )
                )
                break
            base_packet = cast(CxlMemBasePacket, cxl_packet)

            if base_packet.is_s2mndr():
                packet = cast(CxlMemS2MNDRPacket, cxl_packet)
                if packet.s2mndr_header.opcode == CXL_MEM_S2MNDR_OPCODE.CMP_S:
                    status = CACHE_RESPONSE_STATUS.RSP_S
                elif packet.s2mndr_header.opcode == CXL_MEM_S2MNDR_OPCODE.CMP_E:
                    status = CACHE_RESPONSE_STATUS.RSP_I
                elif packet.s2mndr_header.opcode == CXL_MEM_S2MNDR_OPCODE.CMP_M:
                    pass
                else:
                    continue

                if not self._downstream_cxl_mem_fifos.target_to_host.empty():
                    cxl_packet = await self._downstream_cxl_mem_fifos.target_to_host.get()
                    base_packet = cast(CxlMemBasePacket, cxl_packet)
                    if base_packet.is_s2mbisnp():
                        await self._downstream_cxl_mem_fifos.target_to_host.put(cxl_packet)
                        cache_packet = CacheResponse(status)
                    else:
                        assert base_packet.is_s2mdrs()
                        packet = cast(CxlMemS2MDRSPacket, cxl_packet)
                        cache_packet = CacheResponse(status, packet.data)
                else:
                    cache_packet = CacheResponse(status)
                await self._upstream_cache_to_home_agent_fifos.response.put(cache_packet)

            elif base_packet.is_s2mdrs():
                packet = cast(CxlMemS2MDRSPacket, cxl_packet)
                cache_packet = CacheResponse(CACHE_RESPONSE_STATUS.OK, packet.data)
                await self._upstream_cache_to_home_agent_fifos.response.put(cache_packet)

            elif base_packet.is_s2mbisnp():
                packet = cast(CxlMemS2MBISnpPacket, cxl_packet)
                addr = packet.get_address()

                if packet.s2mbisnp_header.opcode == CXL_MEM_S2MBISNP_OPCODE.BISNP_DATA:
                    cache_packet = CacheRequest(CACHE_REQUEST_TYPE.SNP_DATA, addr)
                elif packet.s2mbisnp_header.opcode == CXL_MEM_S2MBISNP_OPCODE.BISNP_INV:
                    cache_packet = CacheRequest(CACHE_REQUEST_TYPE.SNP_INV, addr)
                await self._upstream_home_agent_to_cache_fifos.request.put(cache_packet)
                bi_id = packet.s2mbisnp_header.bi_id
                bi_tag = packet.s2mbisnp_header.bi_tag

                packet = await self._upstream_home_agent_to_cache_fifos.response.get()
                if packet.status == CACHE_RESPONSE_STATUS.RSP_S:
                    rsp_state = CXL_MEM_M2SBIRSP_OPCODE.BIRSP_S
                else:
                    rsp_state = CXL_MEM_M2SBIRSP_OPCODE.BIRSP_I
                cxl_packet = CxlMemBIRspPacket.create(rsp_state, bi_id=bi_id, bi_tag=bi_tag)
                await self._downstream_cxl_mem_fifos.host_to_target.put(cxl_packet)

            else:
                logger.info(self._create_message(packet.cxl_mem_header.msg_class))
                assert False

    async def _run(self):
        tasks = [
            create_task(self._process_memory_io_bridge_requests()),
            create_task(self._process_memory_coh_bridge_requests()),
            create_task(self._process_upstream_host_to_target_packets()),
            create_task(self._process_downstream_target_to_host_packets()),
        ]
        await self._change_status_to_running()
        await gather(*tasks)

    async def _stop(self):
        await self._memory_consumer_io_fifos.request.put(None)
        await self._memory_consumer_coh_fifos.request.put(None)
        await self._upstream_cache_to_home_agent_fifos.request.put(None)
        await self._downstream_cxl_mem_fifos.target_to_host.put(None)
