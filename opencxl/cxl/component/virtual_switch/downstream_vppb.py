"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from dataclasses import dataclass
from typing import Optional

from opencxl.cxl.component.common import CXL_COMPONENT_TYPE
from opencxl.cxl.component.cxl_cache_manager import CxlCacheManager
from opencxl.cxl.component.cxl_connection import CxlConnection
from opencxl.cxl.component.virtual_switch.routing_table import RoutingTable
from opencxl.cxl.component.cxl_mem_manager import CxlMemManager
from opencxl.cxl.component.cxl_io_manager import CxlIoManager
from opencxl.cxl.component.cxl_bridge_component import (
    CxlDownstreamPortComponent,
)
from opencxl.cxl.mmio import CombinedMmioRegister, CombinedMmioRegiterOptions
from opencxl.cxl.component.virtual_switch.vppb import Vppb
from opencxl.cxl.config_space.port import (
    CxlDownstreamPortConfigSpace,
    CxlDownstreamPortConfigSpaceOptions,
)
from opencxl.cxl.config_space.dvsec import (
    DvsecConfigSpaceOptions,
    DvsecRegisterLocatorOptions,
    CXL_DEVICE_TYPE,
)
from opencxl.pci.component.pci import (
    PciComponentIdentity,
    PciBridgeComponent,
    PCI_BRIDGE_TYPE,
    PCI_CLASS,
    PCI_BRIDGE_SUBCLASS,
    PCI_DEVICE_PORT_TYPE,
    EEUM_VID,
    SW_DSP_DID,
)
from opencxl.pci.config_space.pci import REG_ADDR
from opencxl.pci.component.mmio_manager import MmioManager, BarEntry
from opencxl.pci.component.config_space_manager import (
    ConfigSpaceManager,
    PCI_DEVICE_TYPE,
)


@dataclass
class DummyConfig:
    vcs_id: int
    vppb_id: int
    routing_table: RoutingTable


@dataclass
class EnumerationInfo:
    secondary_bus: int
    subordinate_bus: int
    memory_base: int
    memory_limit: int


class DownstreamVppb(Vppb):
    def __init__(self, vppb_index: int, vcs_id: int):
        super().__init__()
        self._vppb_index = vppb_index
        self._vcs_id = vcs_id

    def _get_label(self) -> str:
        vcs_str = f"VCS{self._vcs_id}"
        vppb_str = f"vPPB{self._vppb_index}(DSP)"
        return f"{vcs_str}:{vppb_str}"

    def _create_message(self, message: str) -> str:
        message = f"[{self.__class__.__name__}:{self._get_label()}] {message}"
        return message

    def get_reg_vals(self):
        return self._cxl_io_manager.get_cfg_reg_vals()

    def set_vppb_index(self, vppb_index: int):
        self._vppb_index = vppb_index
        self._pci_bridge_component.set_port_number(self._vppb_index)

    def get_device_type(self) -> CXL_COMPONENT_TYPE:
        return CXL_COMPONENT_TYPE.DSP

    def set_routing_table(self, routing_table: RoutingTable):
        self._pci_bridge_component.set_routing_table(routing_table)

    def backup_enumeration_info(self) -> EnumerationInfo:
        info = EnumerationInfo(
            secondary_bus=self._pci_registers.pci.secondary_bus_number,
            subordinate_bus=self._pci_registers.pci.subordinate_bus_number,
            memory_base=self._pci_registers.pci.memory_base,
            memory_limit=self._pci_registers.pci.memory_limit,
        )
        return info

    def get_secondary_bus_number(self):
        return self._pci_registers.pci.secondary_bus_number

    def restore_enumeration_info(self, info: EnumerationInfo):
        self._pci_registers.write_bytes(
            REG_ADDR.SECONDARY_BUS_NUMBER.START,
            REG_ADDR.SECONDARY_BUS_NUMBER.END,
            info.secondary_bus,
        )
        self._pci_registers.write_bytes(
            REG_ADDR.SUBORDINATE_BUS_NUMBER.START,
            REG_ADDR.SUBORDINATE_BUS_NUMBER.END,
            info.subordinate_bus,
        )
        self._pci_registers.write_bytes(
            REG_ADDR.MEMORY_BASE.START,
            REG_ADDR.MEMORY_BASE.END,
            info.memory_base,
        )
        self._pci_registers.write_bytes(
            REG_ADDR.MEMORY_LIMIT.START,
            REG_ADDR.MEMORY_LIMIT.END,
            info.memory_limit,
        )

    def get_cxl_component(self) -> CxlDownstreamPortComponent:
        return self._cxl_component
