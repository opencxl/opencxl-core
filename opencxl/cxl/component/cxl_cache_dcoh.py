"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from typing import Optional, cast
from asyncio import create_task, gather, sleep, Queue

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
    CXL_CACHE_H2DREQ_OPCODE,
    CXL_CACHE_H2DRSP_OPCODE,
    CXL_CACHE_H2DRSP_CACHE_STATE,
    CXL_CACHE_D2HREQ_OPCODE,
    CXL_CACHE_D2HRSP_OPCODE,
)
from opencxl.cxl.transport.cache_fifo import (
    CacheFifoPair,
    CacheRequest,
    CACHE_REQUEST_TYPE,
    CacheResponse,
    CACHE_RESPONSE_STATUS,
)
from opencxl.cxl.component.cache_controller import (
    CohStateMachine,
    COH_STATE_MACHINE,
)
from opencxl.pci.component.packet_processor import PacketProcessor


class CxlCacheDcoh(PacketProcessor):
    def __init__(
        self,
        cache_to_coh_agent_fifo: CacheFifoPair,
        coh_agent_to_cache_fifo: CacheFifoPair,
        upstream_fifo: FifoPair,
        downstream_fifo: Optional[FifoPair] = None,
        label: Optional[str] = None,
    ):
        # pylint: disable=duplicate-code
        self._label = label
        self._downstream_fifo: Optional[FifoPair]
        self._upstream_fifo: FifoPair

        super().__init__(upstream_fifo, downstream_fifo, label)
        self._cache_to_coh_agent_fifo = cache_to_coh_agent_fifo
        self._coh_agent_to_cache_fifo = coh_agent_to_cache_fifo

        self._cur_state = CohStateMachine(
            state=COH_STATE_MACHINE.COH_STATE_INIT,
            packet=None,
            cache_rsp=CACHE_RESPONSE_STATUS.RSP_I,
            cache_list=[
                0,
            ],
        )

        # emulated .cache d2h channels
        self._cxl_channel = {"h2d_req": Queue(), "h2d_rsp": Queue(), "h2d_data": Queue()}

    # .cache h2d req handler
    async def _process_cxl_h2d_req_packet(self, h2dreq_packet: CxlCacheH2DReqPacket):
        if self._downstream_fifo is not None:
            raise Exception(f"CXL Endpoint Device: {self._label}")

        data_read = False
        addr = h2dreq_packet.get_address()

        if h2dreq_packet.h2dreq_header.cache_opcode == CXL_CACHE_H2DREQ_OPCODE.SNP_DATA:
            type = CACHE_REQUEST_TYPE.SNP_DATA
        elif h2dreq_packet.h2dreq_header.cache_opcode == CXL_CACHE_H2DREQ_OPCODE.SNP_INV:
            type = CACHE_REQUEST_TYPE.SNP_INV
        elif h2dreq_packet.h2dreq_header.cache_opcode == CXL_CACHE_H2DREQ_OPCODE.SNP_CUR:
            type = CACHE_REQUEST_TYPE.SNP_CUR

        cache_packet = CacheRequest(type, addr)
        await self._coh_agent_to_cache_fifo.request.put(cache_packet)
        cache_packet = await self._coh_agent_to_cache_fifo.response.get()

        if cache_packet.status == CACHE_RESPONSE_STATUS.RSP_MISS:
            opcode = CXL_CACHE_D2HRSP_OPCODE.RSP_I_HIT_I

        elif cache_packet.status == CACHE_RESPONSE_STATUS.RSP_I:
            if type == CACHE_REQUEST_TYPE.SNP_INV:
                opcode = CXL_CACHE_D2HRSP_OPCODE.RSP_I_HIT_SE

        elif cache_packet.status in (CACHE_RESPONSE_STATUS.RSP_S, CACHE_RESPONSE_STATUS.RSP_V):
            data_read = True
            if type == CACHE_REQUEST_TYPE.SNP_DATA:
                opcode = CXL_CACHE_D2HRSP_OPCODE.RSP_S_FWD_M
            elif type == CACHE_REQUEST_TYPE.SNP_CUR:
                opcode = CXL_CACHE_D2HRSP_OPCODE.RSP_V_HIT_V
        else:
            raise Exception(f"Received unexpected packet: {h2dreq_packet.get_type()}")

        cxl_packet = CxlCacheCacheD2HRspPacket.create(0, opcode)
        await self._upstream_fifo.target_to_host.put(cxl_packet)

        if data_read is True:
            cxl_packet = CxlCacheCacheD2HDataPacket.create(0, cache_packet.data)
            await self._upstream_fifo.target_to_host.put(cxl_packet)

    # .cache h2d rsp handler
    async def _process_cxl_h2d_rsp_packet(self, h2drsp_packet: CxlCacheH2DRspPacket):
        if self._downstream_fifo is not None:
            raise Exception(f"CXL Endpoint Device: {self._label}")

        if h2drsp_packet.h2drsp_header.cache_opcode == CXL_CACHE_H2DRSP_OPCODE.GO:
            if h2drsp_packet.h2drsp_header.rsp_data == CXL_CACHE_H2DRSP_CACHE_STATE.EXCLUSIVE:
                cache_packet = CacheResponse(CACHE_RESPONSE_STATUS.RSP_I)
                await self._cache_to_coh_agent_fifo.response.put(cache_packet)

            elif h2drsp_packet.h2drsp_header.rsp_data == CXL_CACHE_H2DRSP_CACHE_STATE.SHARED:
                if self._cxl_channel["h2d_data"].empty():
                    return
                packet = await self._cxl_channel["h2d_data"].get()
                cache_packet = CacheResponse(CACHE_RESPONSE_STATUS.RSP_S, packet.data)
                await self._cache_to_coh_agent_fifo.response.put(cache_packet)

            elif h2drsp_packet.h2drsp_header.rsp_data == CXL_CACHE_H2DRSP_CACHE_STATE.INVALID:
                pass

        elif h2drsp_packet.h2drsp_header.cache_opcode == CXL_CACHE_H2DRSP_OPCODE.GO_WRITE_PULL:
            cxl_packet = CxlCacheCacheD2HDataPacket.create(0, self._cur_state.packet.data)
            await self._upstream_fifo.target_to_host.put(cxl_packet)
            cache_packet = CacheResponse(CACHE_RESPONSE_STATUS.OK)
            await self._cache_to_coh_agent_fifo.response.put(cache_packet)

        self._cur_state.state = COH_STATE_MACHINE.COH_STATE_INIT

    # .cache d2h device req handler
    async def _process_cache_to_dcoh(self, cache_packet: CacheRequest):
        if self._cur_state.state == COH_STATE_MACHINE.COH_STATE_WAIT:
            return

        if self._cur_state.state == COH_STATE_MACHINE.COH_STATE_START:
            addr = cache_packet.address

            if cache_packet.type == CACHE_REQUEST_TYPE.WRITE_BACK:
                cxl_packet = CxlCacheCacheD2HReqPacket.create(
                    addr, self._cur_state.cache_list[0], CXL_CACHE_D2HREQ_OPCODE.CACHE_DIRTY_EVICT
                )
            elif cache_packet.type == CACHE_REQUEST_TYPE.SNP_DATA:
                cxl_packet = CxlCacheCacheD2HReqPacket.create(
                    addr, self._cur_state.cache_list[0], CXL_CACHE_D2HREQ_OPCODE.CACHE_RD_SHARED
                )
            elif cache_packet.type == CACHE_REQUEST_TYPE.SNP_INV:
                cxl_packet = CxlCacheCacheD2HReqPacket.create(
                    addr,
                    self._cur_state.cache_list[0],
                    CXL_CACHE_D2HREQ_OPCODE.CACHE_RD_OWN_NO_DATA,
                )
            await self._upstream_fifo.target_to_host.put(cxl_packet)
            self._cur_state.state = COH_STATE_MACHINE.COH_STATE_WAIT

    # .cache h2d host packet handler
    async def _process_host_to_target(self):
        # pylint: disable=duplicate-code
        logger.debug(self._create_message("Started processing incoming fifo from host"))
        while True:
            packet = await self._upstream_fifo.host_to_target.get()
            if packet is None:
                logger.debug(self._create_message("Stopped processing incoming fifo from host"))
                break

            base_packet = cast(BasePacket, packet)
            if not base_packet.is_cxl_cache():
                raise Exception(f"Received unexpected packet: {base_packet.get_type()}")

            cxl_packet = cast(CxlCacheBasePacket, packet)
            if cxl_packet.is_h2dreq():
                await self._cxl_channel["h2d_req"].put(cast(CxlCacheH2DReqPacket, packet))
            elif cxl_packet.is_h2drsp():
                await self._cxl_channel["h2d_rsp"].put(cast(CxlCacheH2DRspPacket, packet))
            elif cxl_packet.is_h2ddata():
                await self._cxl_channel["h2d_data"].put(cast(CxlCacheH2DDataPacket, packet))
            else:
                raise Exception(f"Received unexpected packet: {cxl_packet.get_type()}")

    # process from host/device channels simultaneously
    async def _cxl_cache_dcoh_main_loop(self):
        _stop_process = False

        while not _stop_process:
            await sleep(0)
            # fetch device request packet
            if self._cur_state.state == COH_STATE_MACHINE.COH_STATE_INIT:
                if not self._cache_to_coh_agent_fifo.request.empty():
                    self._cur_state.packet = await self._cache_to_coh_agent_fifo.request.get()
                    if self._cur_state.packet is None:
                        logger.debug(
                            self._create_message("Stop processing cxl cache dcoh main loop")
                        )
                        _stop_process = True
                    self._cur_state.state = COH_STATE_MACHINE.COH_STATE_START

            # run request processing and response checking code continuously until state changed
            # data packets are extracted and consumed in request processing code
            else:
                await self._process_cache_to_dcoh(self._cur_state.packet)

                if not self._cxl_channel["h2d_rsp"].empty():
                    packet = await self._cxl_channel["h2d_rsp"].get()
                    await self._process_cxl_h2d_rsp_packet(packet)

                # process host request regardless of device processing state
                if not self._cxl_channel["h2d_req"].empty():
                    packet = await self._cxl_channel["h2d_req"].get()
                    # corner case handling
                    if packet.get_address() == self._cur_state.packet.address:
                        assert self._cur_state.packet.type == CACHE_REQUEST_TYPE.WRITE_BACK
                        if packet.h2dreq_header.cache_opcode == CXL_CACHE_H2DREQ_OPCODE.SNP_INV:
                            cxl_packet = CxlCacheCacheD2HRspPacket.create(
                                0, CXL_CACHE_D2HRSP_OPCODE.RSP_I_HIT_I
                            )
                            await self._upstream_fifo.target_to_host.put(cxl_packet)
                            continue
                    await self._process_cxl_h2d_req_packet(packet)

    # pylint: disable=duplicate-code
    async def _run(self):
        tasks = [
            create_task(self._process_host_to_target()),
            create_task(self._cxl_cache_dcoh_main_loop()),
        ]
        await self._change_status_to_running()
        await gather(*tasks)

    async def _stop(self):
        await self._upstream_fifo.host_to_target.put(None)
        await self._cache_to_coh_agent_fifo.request.put(None)
