"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

# pylint: disable=unused-import
from asyncio import CancelledError, Event, Future, Lock, create_task, get_running_loop, timeout
from collections.abc import Callable
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

        self.device_entries = {}  # maps CQID -> future host UQID
        self.device_entry_lock = Lock()  # locks the above mapping

        super().__init__(upstream_fifo, downstream_fifo, label)

    def set_memory_device_component(self, cache_device_component: CxlMemoryDeviceComponent):
        self._cache_device_component = cache_device_component

    async def register_cqid_listener(
        self,
        cqid: int,
        cb: Callable[[BasePacket], None],
        _timeout: Optional[int] = None,
        _one_use: bool = True,
    ):
        """
        Registers a callback action upon reception of a H2DRSP/H2DREQ packet with matching CQID.
        Used to emulate the "device entry" model in CXL.cache spec. The listener should accept
        a single CxlBasePacket parameter, which will contain the H2D packet object.

        If `_one_use` is True, the callback action will automatically unregister itself after
        one call. Note that if `_one_use` is false, the caller will have to manually handle
        cancelling by raising CancelledError, just as they would for a generic asyncio Task.

        If the operation times out, the callback action unconditionally unregisters itself.
        """
        async with self.device_entry_lock:
            # two threads accessing self.device_entries simultaneously
            # has the potential to SERIOUSLY break the emulator
            if cqid in self.device_entries:
                # cancel any currently running listeners, if they exist
                self.device_entries[cqid].cancel()

        loop = get_running_loop()
        fut_uqid = loop.create_future()

        async def _callback(fut: Future):
            while True:
                if _timeout:
                    try:
                        async with timeout(_timeout):
                            await fut.wait()
                    except TimeoutError as err:
                        # this request was apparently lost by the host
                        # clear the cqid entry for reuse
                        async with self.device_entry_lock:
                            del self.device_entries[cqid]
                        raise CancelledError from err  # intentionally cancel the current task
                else:
                    fut.wait()
                cb(fut.result())
                if _one_use:
                    async with self.device_entry_lock:
                        del self.device_entries[cqid]
                    break

        self.device_entries[cqid] = fut_uqid
        create_task(_callback(fut_uqid))

    async def _process_cxl_cache_h2d_data_packet(self, cache_data_packet: CxlCacheH2DDataPacket):
        await self._upstream_fifo.host_to_target.put(cache_data_packet)

    async def send_d2h_req_test(self):
        # Test func 1: Sending D2H Req
        # Addr must be aligned to 0x40
        # cache_id is 4-bit
        # Some arbitrary opcode
        packet = CxlCacheCacheD2HReqPacket.create(
            addr=0x40, cache_id=0b1010, opcode=CXL_CACHE_D2HREQ_OPCODE.CACHE_RD_ANY
        )
        await self._upstream_fifo.target_to_host.put(packet)

    async def send_d2h_req_rdown(self, addr: int):
        # Cache ID is "filled in" by the switch
        packet = CxlCacheCacheD2HReqPacket.create(
            addr=addr, cache_id=0, opcode=CXL_CACHE_D2HREQ_OPCODE.CACHE_RD_OWN
        )
        await self._upstream_fifo.target_to_host.put(packet)

    async def send_d2h_req_itomwr(self, addr: int, cqid: int):
        packet = CxlCacheCacheD2HReqPacket.create(
            addr=addr, cache_id=0, opcode=CXL_CACHE_D2HREQ_OPCODE.CACHE_I_TO_M_WR, cqid=cqid
        )
        await self._upstream_fifo.target_to_host.put(packet)

    async def send_d2h_data(self, data: int, uqid: int):
        packet = CxlCacheCacheD2HDataPacket.create(
            uqid=uqid,
            data=data,
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

                # TODO: Move this logic out into a generalized process_h2d_rsp method.

                # notify matching cqid listener, if one exists
                cqid = h2drsp_packet.h2drsp_header.cq_id
                async with self.device_entry_lock:
                    if cqid in self.device_entries:
                        self.device_entries[cqid].set_result(h2drsp_packet)

                print("Received h2drsp_packet:")
                h2drsp_packet.get_pretty_string()
            elif cxl_cache_packet.is_h2ddata():
                h2ddata_packet = cast(CxlCacheCacheH2DDataPacket, packet)
                cqid = h2ddata_packet.h2drsp_header.cq_id
                async with self.device_entry_lock:
                    if cqid in self.device_entries:
                        self.device_entries[cqid].set_result(h2ddata_packet)
                await self._process_cxl_cache_h2d_data_packet(h2ddata_packet)
            else:
                raise Exception(f"Received unexpected packet: {base_packet.get_type()}")
