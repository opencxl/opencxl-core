"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from typing import Optional

from opencxl.pci.component.routing_table import PciRoutingTable
from opencxl.cxl.component.hdm_decoder import SwitchHdmDecoderManager


class RoutingTable(PciRoutingTable):
    def __init__(self, table_size: int, label: Optional[str] = None):
        super().__init__(table_size, label=label)
        self._hdm_decoder_manager: Optional[SwitchHdmDecoderManager] = None

    def set_hdm_decoder(self, hdm_decoder_manager: SwitchHdmDecoderManager):
        self._hdm_decoder_manager = hdm_decoder_manager

    def get_cxl_mem_target_port(self, memory_addr: int) -> Optional[int]:
        if self._hdm_decoder_manager is None:
            raise Exception("HDM Decoder Manager is not initialized")
        return self._hdm_decoder_manager.get_target(memory_addr)
