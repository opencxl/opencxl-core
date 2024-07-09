"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from typing import Optional, Tuple, cast
from asyncio import create_task, gather
from enum import Enum, auto

from opencxl.util.logger import logger
from opencxl.pci.component.fifo_pair import FifoPair
from opencxl.cxl.transport.transaction import (
    BasePacket,
    CxlMemBasePacket,
    CxlMemM2SReqPacket,
    CxlMemM2SRwDPacket,
    CxlMemM2SBIRspPacket,
    CxlMemMemDataPacket,
    CxlMemCmpPacket,
    CxlMemBISnpPacket,
    CXL_MEM_META_FIELD,
    CXL_MEM_META_VALUE,
    CXL_MEM_M2S_SNP_TYPE,
    CXL_MEM_M2SREQ_OPCODE,
    CXL_MEM_M2SBIRSP_OPCODE,
    CXL_MEM_S2MNDR_OPCODE,
    CXL_MEM_S2MDRS_OPCODE,
    CXL_MEM_S2MBISNP_OPCODE,
)
from opencxl.cxl.transport.cache_fifo import (
    CacheFifoPair,
    CacheRequest,
    CACHE_REQUEST_TYPE,
    CacheResponse,
    CACHE_RESPONSE_STATUS,
)
from opencxl.cxl.component.cxl_memory_device_component import CxlMemoryDeviceComponent
from opencxl.pci.component.packet_processor import PacketProcessor


# snoop filter update type definition
# both cache's snoop filter insertion/deletion
class SF_UPDATE_TYPE(Enum):
    SF_HOST_IN = auto()
    SF_HOST_OUT = auto()
    SF_DEVICE_IN = auto()
    SF_DEVICE_OUT = auto()


class CxlMemDcoh(PacketProcessor):
    def __init__(
        self,
        cache_to_coh_agent_fifo: CacheFifoPair,
        coh_agent_to_cache_fifo: CacheFifoPair,
        upstream_fifo: FifoPair,
        downstream_fifo: Optional[FifoPair] = None,
        label: Optional[str] = None,
    ):
        self._downstream_fifo: Optional[FifoPair]
        self._upstream_fifo: FifoPair

        super().__init__(upstream_fifo, downstream_fifo, label)
        self._cache_to_coh_agent_fifo = cache_to_coh_agent_fifo
        self._coh_agent_to_cache_fifo = coh_agent_to_cache_fifo
        self._memory_device_component: Optional[CxlMemoryDeviceComponent] = None

        # snoop filter defined as set structure
        # max sf size will be the same as each cache size
        self._sf_host = set()
        self._sf_device = set()
        self._addr = 0

    def set_memory_device_component(self, memory_device_component: CxlMemoryDeviceComponent):
        self._memory_device_component = memory_device_component

    def _snoop_filter_update(self, addr, sf_update_list) -> None:
        for sf_type in sf_update_list:
            if sf_type == SF_UPDATE_TYPE.SF_HOST_IN:
                self._sf_host.add(addr)
            elif sf_type == SF_UPDATE_TYPE.SF_HOST_OUT:
                self._sf_host.remove(addr)
            elif sf_type == SF_UPDATE_TYPE.SF_DEVICE_IN:
                self._sf_device.add(addr)
            elif sf_type == SF_UPDATE_TYPE.SF_DEVICE_OUT:
                self._sf_device.remove(addr)

    def _sf_host_is_hit(self, addr) -> bool:
        return addr in self._sf_host

    def _sf_device_is_hit(self, addr) -> bool:
        return addr in self._sf_device

    # .mem response may have two packets (nds and drs)
    # this method always makes two reponse packets but caller may ignore one if possible
    def _create_mem_rsp_packet(
        self,
        ndr_opcode: CXL_MEM_S2MNDR_OPCODE,
        data: Optional[int] = 0,
        drs_opcode: Optional[CXL_MEM_S2MDRS_OPCODE] = CXL_MEM_S2MDRS_OPCODE.MEM_DATA,
        meta_field: Optional[CXL_MEM_META_FIELD] = CXL_MEM_META_FIELD.NO_OP,
        meta_value: Optional[CXL_MEM_META_VALUE] = CXL_MEM_META_VALUE.ANY,
    ) -> Tuple[CxlMemCmpPacket, CxlMemMemDataPacket]:
        return (
            CxlMemCmpPacket.create(ndr_opcode, meta_field, meta_value),
            CxlMemMemDataPacket.create(data, drs_opcode, meta_field, meta_value),
        )

    # .mem m2s req (MemRd, MemRdData, and MemInv) handler
    async def _process_cxl_m2s_req_packet(self, m2sreq_packet: CxlMemM2SReqPacket):
        if self._downstream_fifo is not None:
            logger.debug(self._create_message("Forwarding CXL.mem M2S Req packet"))
            await self._downstream_fifo.host_to_target.put(m2sreq_packet)
            return

        if self._memory_device_component is None:
            raise Exception("CxlMemoryDeviceComponent isn't set yet")

        addr = m2sreq_packet.get_address()

        if m2sreq_packet.m2sreq_header.meta_field == CXL_MEM_META_FIELD.NO_OP:
            data = await self._memory_device_component.read_mem(addr)

            dummy, packet = self._create_mem_rsp_packet(CXL_MEM_S2MNDR_OPCODE.CMP, data)
            await self._upstream_fifo.target_to_host.put(packet)
            return

        rsp_code = CXL_MEM_S2MNDR_OPCODE.CMP
        sf_update_list = []
        data = 0
        data_flush = False
        data_read = m2sreq_packet.m2sreq_header.mem_opcode in (
            CXL_MEM_M2SREQ_OPCODE.MEM_RD,
            CXL_MEM_M2SREQ_OPCODE.MEM_RD_DATA,
        )

        # device cache snoop filter miss
        if not self._sf_device_is_hit(addr):
            if m2sreq_packet.m2sreq_header.snp_type == CXL_MEM_M2S_SNP_TYPE.SNP_DATA:
                rsp_code = CXL_MEM_S2MNDR_OPCODE.CMP_E
                sf_update_list.append(SF_UPDATE_TYPE.SF_HOST_IN)
            elif m2sreq_packet.m2sreq_header.snp_type == CXL_MEM_M2S_SNP_TYPE.SNP_INV:
                if m2sreq_packet.m2sreq_header.meta_value == CXL_MEM_META_VALUE.ANY:
                    rsp_code = CXL_MEM_S2MNDR_OPCODE.CMP_E
                    sf_update_list.append(SF_UPDATE_TYPE.SF_HOST_IN)
                elif (
                    m2sreq_packet.m2sreq_header.meta_value == CXL_MEM_META_VALUE.INVALID
                    or m2sreq_packet.m2sreq_header.meta_field == CXL_MEM_META_FIELD.NO_OP
                ):
                    pass
            elif m2sreq_packet.m2sreq_header.snp_type == CXL_MEM_M2S_SNP_TYPE.SNP_CUR:
                pass

            if data_read is True:
                data = await self._memory_device_component.read_mem(addr)
        # device cache snoop filter hit
        else:
            if m2sreq_packet.m2sreq_header.snp_type == CXL_MEM_M2S_SNP_TYPE.SNP_DATA:
                type = CACHE_REQUEST_TYPE.SNP_DATA
            elif m2sreq_packet.m2sreq_header.snp_type == CXL_MEM_M2S_SNP_TYPE.SNP_INV:
                type = CACHE_REQUEST_TYPE.SNP_INV
            elif m2sreq_packet.m2sreq_header.snp_type == CXL_MEM_M2S_SNP_TYPE.SNP_CUR:
                type = CACHE_REQUEST_TYPE.SNP_CUR

            packet = CacheRequest(type, addr)
            await self._coh_agent_to_cache_fifo.request.put(packet)

            # corner case - cache status changed at the same addr
            packet = await self._coh_agent_to_cache_fifo.response.get()
            if packet.status == CACHE_RESPONSE_STATUS.RSP_MISS:
                await self._upstream_fifo.host_to_target.put(m2sreq_packet)
                return

            if packet.status == CACHE_RESPONSE_STATUS.RSP_S:
                rsp_code = CXL_MEM_S2MNDR_OPCODE.CMP_S
                sf_update_list.append(SF_UPDATE_TYPE.SF_HOST_IN)
            elif packet.status == CACHE_RESPONSE_STATUS.RSP_I:
                if m2sreq_packet.m2sreq_header.meta_value == CXL_MEM_META_VALUE.ANY:
                    rsp_code = CXL_MEM_S2MNDR_OPCODE.CMP_E
                    sf_update_list.append(SF_UPDATE_TYPE.SF_HOST_IN)
                    sf_update_list.append(SF_UPDATE_TYPE.SF_DEVICE_OUT)
                elif (
                    m2sreq_packet.m2sreq_header.meta_value == CXL_MEM_META_VALUE.INVALID
                    or m2sreq_packet.m2sreq_header.meta_field == CXL_MEM_META_FIELD.NO_OP
                ):
                    sf_update_list.append(SF_UPDATE_TYPE.SF_DEVICE_OUT)
                    if m2sreq_packet.m2sreq_header.mem_opcode == CXL_MEM_M2SREQ_OPCODE.MEM_RD:
                        data_flush = True
            elif packet.status == CACHE_RESPONSE_STATUS.RSP_V:
                pass
            data = packet.data

        if sf_update_list:
            self._snoop_filter_update(addr, sf_update_list)

        if data_flush is True:
            await self._memory_device_component.write_mem(addr, data)

        ndr_packet, drs_packet = self._create_mem_rsp_packet(rsp_code, data)
        await self._upstream_fifo.target_to_host.put(ndr_packet)

        if data_read is True:
            await self._upstream_fifo.target_to_host.put(drs_packet)

    # .mem m2s rwd (MemWr) handler
    async def _process_cxl_m2s_rwd_packet(self, m2srwd_packet: CxlMemM2SRwDPacket):
        if self._downstream_fifo is not None:
            logger.debug(self._create_message("Forwarding CXL.mem M2S RwD packet"))
            await self._downstream_fifo.host_to_target.put(m2srwd_packet)
            return

        if self._memory_device_component is None:
            raise Exception("CxlMemoryDeviceComponent isn't set yet")

        addr = m2srwd_packet.get_address()

        if m2srwd_packet.m2srwd_header.meta_field == CXL_MEM_META_FIELD.NO_OP:
            await self._memory_device_component.write_mem(addr, m2srwd_packet.data)

            packet, dummy = self._create_mem_rsp_packet(
                CXL_MEM_S2MNDR_OPCODE.CMP, m2srwd_packet.data
            )
            await self._upstream_fifo.target_to_host.put(packet)
            return

        rsp_code = CXL_MEM_S2MNDR_OPCODE.CMP
        sf_update_list = []
        data_flush = True

        # device cache snoop filter miss
        if not self._sf_device_is_hit(addr):
            if m2srwd_packet.m2srwd_header.meta_value == CXL_MEM_META_VALUE.INVALID:
                sf_update_list.append(SF_UPDATE_TYPE.SF_HOST_OUT)
        # device cache snoop filter hit
        else:
            if m2srwd_packet.m2srwd_header.meta_value == CXL_MEM_META_VALUE.ANY:
                packet = CacheRequest(CACHE_REQUEST_TYPE.SNP_INV, addr)
                sf_update_list.append(SF_UPDATE_TYPE.SF_DEVICE_OUT)
            elif m2srwd_packet.m2srwd_header.meta_value == CXL_MEM_META_VALUE.SHARED:
                packet = CacheRequest(CACHE_REQUEST_TYPE.SNP_DATA, addr)
            elif m2srwd_packet.m2srwd_header.meta_value == CXL_MEM_META_VALUE.INVALID:
                packet = CacheRequest(CACHE_REQUEST_TYPE.WRITE_BACK, addr)
                sf_update_list.append(SF_UPDATE_TYPE.SF_HOST_OUT)
            await self._coh_agent_to_cache_fifo.request.put(packet)

            packet = await self._coh_agent_to_cache_fifo.response.get()
            if packet.status == CACHE_RESPONSE_STATUS.RSP_MISS:
                ndr_packet, dummy = self._create_mem_rsp_packet(rsp_code)
                await self._upstream_fifo.target_to_host.put(ndr_packet)
                return

        if sf_update_list:
            self._snoop_filter_update(addr, sf_update_list)

        if data_flush is True:
            await self._memory_device_component.write_mem(addr, m2srwd_packet.data)

        ndr_packet, dummy = self._create_mem_rsp_packet(rsp_code)
        await self._upstream_fifo.target_to_host.put(ndr_packet)

    async def _process_host_to_target(self):
        logger.debug(self._create_message("Started processing incoming fifo from host"))
        while True:
            packet = await self._upstream_fifo.host_to_target.get()
            if packet is None:
                logger.debug(self._create_message("Stopped processing incoming fifo from host"))
                break

            base_packet = cast(BasePacket, packet)
            if not base_packet.is_cxl_mem():
                raise Exception(f"Received unexpected packet: {base_packet.get_type()}")

            logger.debug(self._create_message("Received incoming packet from host"))
            cxl_mem_packet = cast(CxlMemBasePacket, packet)

            if cxl_mem_packet.is_m2sreq():
                m2sreq_packet = cast(CxlMemM2SReqPacket, packet)
                if m2sreq_packet.is_mem_rd() or m2sreq_packet.is_mem_inv():
                    await self._process_cxl_m2s_req_packet(m2sreq_packet)
                else:
                    raise Exception(
                        f"Unsupported M2SReq Opcode: {m2sreq_packet.m2sreq_header.mem_opcode}"
                    )
            elif cxl_mem_packet.is_m2srwd():
                m2srwd_packet = cast(CxlMemM2SRwDPacket, packet)
                if m2srwd_packet.is_mem_wr():
                    await self._process_cxl_m2s_rwd_packet(m2srwd_packet)
                else:
                    raise Exception(
                        f"Unsupported M2SRwd Opcode: {m2srwd_packet.m2srwd_header.mem_opcode}"
                    )
            elif cxl_mem_packet.is_m2sbirsp():
                m2sbirsp_packet = cast(CxlMemM2SBIRspPacket, packet)
                data = await self._memory_device_component.read_mem(self._addr)
                if m2sbirsp_packet.m2sbirsp_header.opcode == CXL_MEM_M2SBIRSP_OPCODE.BIRSP_S:
                    packet = CacheResponse(CACHE_RESPONSE_STATUS.RSP_S, data)
                elif m2sbirsp_packet.m2sbirsp_header.opcode == CXL_MEM_M2SBIRSP_OPCODE.BIRSP_I:
                    packet = CacheResponse(CACHE_RESPONSE_STATUS.RSP_I, data)
                else:
                    raise Exception(
                        f"Unsupported M2SBIRsp Opcode: {m2sbirsp_packet.m2sbirsp_header.opcode}"
                    )
                await self._cache_to_coh_agent_fifo.response.put(packet)
            else:
                raise Exception(f"Received unexpected packet: {base_packet.get_type()}")

    # .mem s2m bisnp handler
    async def _process_cache_to_dcoh(self):
        logger.debug(self._create_message("Started processing incoming fifo from device cache"))
        while True:
            packet = await self._cache_to_coh_agent_fifo.request.get()
            if packet is None:
                logger.debug(
                    self._create_message("Stopped processing incoming fifo from device cache")
                )
                break

            if self._memory_device_component is None:
                raise Exception("CxlMemoryDeviceComponent isn't set yet")

            logger.debug(self._create_message("Received incoming packet from device cache"))

            addr = packet.address
            sf_update_list = []

            if packet.type == CACHE_REQUEST_TYPE.READ:
                data = await self._memory_device_component.read_mem(addr)
                packet = CacheResponse(CACHE_RESPONSE_STATUS.OK, data)
                await self._cache_to_coh_agent_fifo.response.put(packet)
            elif packet.type == CACHE_REQUEST_TYPE.WRITE:
                await self._memory_device_component.write_mem(addr, packet.data)
            else:
                # host cache snoop filter miss
                if not self._sf_host_is_hit(addr):
                    if packet.type == CACHE_REQUEST_TYPE.WRITE_BACK:
                        sf_update_list.append(SF_UPDATE_TYPE.SF_DEVICE_OUT)
                        await self._memory_device_component.write_mem(addr, packet.data)
                    elif packet.type == CACHE_REQUEST_TYPE.SNP_DATA:
                        sf_update_list.append(SF_UPDATE_TYPE.SF_DEVICE_IN)
                        data = await self._memory_device_component.read_mem(addr)
                        packet = CacheResponse(CACHE_RESPONSE_STATUS.RSP_I, data)
                        await self._cache_to_coh_agent_fifo.response.put(packet)
                    elif packet.type == CACHE_REQUEST_TYPE.SNP_INV:
                        sf_update_list.append(SF_UPDATE_TYPE.SF_DEVICE_IN)
                        packet = CacheResponse(CACHE_RESPONSE_STATUS.RSP_I)
                        await self._cache_to_coh_agent_fifo.response.put(packet)
                # host cache snoop filter hit
                else:
                    self._addr = addr
                    if packet.type == CACHE_REQUEST_TYPE.WRITE_BACK:
                        sf_update_list.append(SF_UPDATE_TYPE.SF_DEVICE_OUT)
                        await self._memory_device_component.write_mem(addr, packet.data)
                    elif packet.type == CACHE_REQUEST_TYPE.SNP_DATA:
                        sf_update_list.append(SF_UPDATE_TYPE.SF_DEVICE_IN)
                        cxl_packet = CxlMemBISnpPacket.create(
                            CXL_MEM_S2MBISNP_OPCODE.BISNP_DATA, addr
                        )
                        await self._upstream_fifo.target_to_host.put(cxl_packet)
                    elif packet.type == CACHE_REQUEST_TYPE.SNP_INV:
                        sf_update_list.append(SF_UPDATE_TYPE.SF_HOST_OUT)
                        sf_update_list.append(SF_UPDATE_TYPE.SF_DEVICE_IN)
                        cxl_packet = CxlMemBISnpPacket.create(
                            CXL_MEM_S2MBISNP_OPCODE.BISNP_INV, addr
                        )
                        await self._upstream_fifo.target_to_host.put(cxl_packet)

                if sf_update_list:
                    self._snoop_filter_update(addr, sf_update_list)

    async def _run(self):
        tasks = [
            create_task(self._process_host_to_target()),
            create_task(self._process_cache_to_dcoh()),
        ]
        await self._change_status_to_running()
        await gather(*tasks)

    async def _stop(self):
        await self._cache_to_coh_agent_fifo.response.put(None)
