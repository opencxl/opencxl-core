"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

# pylint: disable=unused-import
from dataclasses import dataclass
from enum import IntEnum
import os
import time
from typing import TypedDict, List, Optional

from opencxl.util.logger import logger
from opencxl.util.unaligned_bit_structure import (
    UnalignedBitStructure,
    ByteField,
)
from opencxl.cxl.features.event_manager import EventManager
from opencxl.cxl.features.log_manager import LogManager
from opencxl.cxl.features.mailbox import (
    CxlMailbox,
    MailboxCapabilities,
    MIN_PAYLOAD_SIZE,
    MAILBOX_TYPE,
    CxlMailboxCommandBase,
)
from opencxl.cxl.cci.generic.events import (
    GetEventRecords,
    ClearEventRecords,
    GetEventInterruptPolicy,
    SetEventInterruptPolicy,
)
from opencxl.cxl.cci.generic.logs import GetLog, GetSupportedLogs
from opencxl.cxl.component.cxl_component import (
    CxlDeviceComponent,
    CXL_COMPONENT_TYPE,
    CXL_DEVICE_CAPABILITY_TYPE,
)
from opencxl.cxl.component.hdm_decoder import (
    DeviceHdmDecoderManager,
    HdmDecoderManagerBase,
    HdmDecoderCapabilities,
    HDM_DECODER_COUNT,
)


@dataclass
class CXLCacheCacheLineInfo:
    cache_id: int
    last_access_time: float
    state: int = 0
    dirty: bool = False
    data: int = 0

    def write(self, data: int):
        self.data = data

    def read(self) -> int:
        return self.data


class CxlCacheDeviceComponent(CxlDeviceComponent):
    # pylint: disable=duplicate-code
    def __init__(
        self,
        decoder_count: HDM_DECODER_COUNT = HDM_DECODER_COUNT.DECODER_1,
        label: Optional[str] = None,
    ):
        super().__init__(label)
        self._event_manager = EventManager()
        self._log_manager = LogManager()
        primary_mailbox_capabilities = MailboxCapabilities(
            payload_size=MIN_PAYLOAD_SIZE,
            mb_doorbell_interrupt_capable=0,
            background_command_complete_interrupt_capable=0,
            interrupt_message_number=0,
            mailbox_ready_time=0,
            type=MAILBOX_TYPE.MEMORY_DEVICE_COMMANDS,
        )
        primary_mailbox_commands: List[CxlMailboxCommandBase] = [
            GetEventRecords(self._event_manager),
            ClearEventRecords(self._event_manager),
            GetEventInterruptPolicy(self._event_manager),
            SetEventInterruptPolicy(self._event_manager),
            GetLog(self._log_manager),
            GetSupportedLogs(self._log_manager),
        ]
        self._primary_mailbox = CxlMailbox(
            capabilities=primary_mailbox_capabilities, commands=primary_mailbox_commands
        )
        self._log_manager.set_command_effects_log(primary_mailbox_commands)
        hdm_decoder_capabilities = HdmDecoderCapabilities(
            decoder_count=decoder_count,
            target_count=0,
            a11to8_interleave_capable=0,
            a14to12_interleave_capable=0,
            poison_on_decoder_error_capability=0,
            three_six_twelve_way_interleave_capable=0,
            sixteen_way_interleave_capable=0,
            uio_capable=0,
            uio_capable_decoder_count=0,
            mem_data_nxm_capable=0,
        )
        self._hdm_decoder_manager = DeviceHdmDecoderManager(hdm_decoder_capabilities, label=label)
        self._cache_info: List[CXLCacheCacheLineInfo] = []
        for i in range(64):
            self._cache_info.append(
                CXLCacheCacheLineInfo(
                    cache_id=i,
                    last_access_time=time.time(),
                )
            )

    def get_primary_mailbox(self) -> Optional[CxlMailbox]:
        return self._primary_mailbox

    def get_hdm_decoder_manager(self) -> Optional[HdmDecoderManagerBase]:
        return self._hdm_decoder_manager

    def get_component_type(self) -> CXL_COMPONENT_TYPE:
        return CXL_COMPONENT_TYPE.LD

    # Not a memory device - unlike cxl_mem
    def get_capability_type(self) -> CXL_DEVICE_CAPABILITY_TYPE:
        return CXL_DEVICE_CAPABILITY_TYPE.INFER_PCI_CLASS_CODE

    def get_event_manager(self) -> EventManager:
        return self._event_manager

    def get_log_manager(self) -> LogManager:
        return self._log_manager

    async def write_cache(self, cache_id: int, data: int):
        self._cache_info[cache_id].write(data)

    async def read_cache(self, cache_id: int) -> int:
        return self._cache_info[cache_id].read()
