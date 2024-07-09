"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from dataclasses import dataclass
from typing import Optional

from opencxl.cxl.component.cxl_component_type import CXL_COMPONENT_TYPE
from opencxl.cxl.component.cxl_connection import CxlConnection
from opencxl.cxl.component.virtual_switch.routing_table import RoutingTable
from opencxl.cxl.component.cxl_mem_manager import CxlMemManager
from opencxl.cxl.component.cxl_io_manager import CxlIoManager
from opencxl.cxl.component.cxl_bridge_component import (
    CxlDownstreamPortComponent,
)
from opencxl.cxl.mmio import CombinedMmioRegister, CombinedMmioRegiterOptions
from opencxl.cxl.device.port_device import CxlPortDevice
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


class DownstreamPortDevice(CxlPortDevice):
    def __init__(
        self,
        transport_connection: CxlConnection,
        port_index: int = 0,
        dummy_config: Optional[DummyConfig] = None,
    ):
        if dummy_config is not None:
            self._vcs_id = dummy_config.vcs_id
            self._vppb_index = dummy_config.vppb_id
        else:
            self._vppb_index: Optional[int] = None

        super().__init__(transport_connection, port_index)

        self._dummy_config = dummy_config
        self._is_dummy = dummy_config is not None
        self._pci_bridge_component = None
        self._pci_registers = None
        self._cxl_component = None
        self._upstream_connection = CxlConnection()

        self._cxl_io_manager = CxlIoManager(
            self._upstream_connection.mmio_fifo,
            self._transport_connection.mmio_fifo,
            self._upstream_connection.cfg_fifo,
            self._transport_connection.cfg_fifo,
            device_type=PCI_DEVICE_TYPE.DOWNSTREAM_BRIDGE,
            init_callback=self._init_device,
            label=self._get_label(),
        )
        self._cxl_mem_manager = CxlMemManager(
            upstream_fifo=self._upstream_connection.cxl_mem_fifo,
            downstream_fifo=transport_connection.cxl_mem_fifo,
            label=self._get_label(),
        )

    def _init_device(
        self,
        mmio_manager: MmioManager,
        config_space_manager: ConfigSpaceManager,
    ):
        pci_identity = PciComponentIdentity(
            vendor_id=EEUM_VID,
            device_id=SW_DSP_DID,
            base_class_code=PCI_CLASS.BRIDGE,
            sub_class_coce=PCI_BRIDGE_SUBCLASS.PCI_BRIDGE,
            programming_interface=0x00,
            device_port_type=PCI_DEVICE_PORT_TYPE.DOWNSTREAM_PORT_OF_PCI_EXPRESS_SWITCH,
        )
        self._pci_bridge_component = PciBridgeComponent(
            identity=pci_identity,
            type=PCI_BRIDGE_TYPE.DOWNSTREAM_PORT,
            mmio_manager=mmio_manager,
            label=self._get_label(),
        )
        if self._is_dummy:
            self._pci_bridge_component.set_port_number(self._dummy_config.vppb_id)
            self._pci_bridge_component.set_routing_table(self._dummy_config.routing_table)

        # Create MMIO register
        cxl_component = CxlDownstreamPortComponent()
        self._cxl_component = cxl_component
        mmio_options = CombinedMmioRegiterOptions(cxl_component=cxl_component)
        mmio_register = CombinedMmioRegister(options=mmio_options)
        mmio_manager.set_bar_entries([BarEntry(mmio_register)])

        # Create Config Space Register
        pci_registers_options = CxlDownstreamPortConfigSpaceOptions(
            pci_bridge_component=self._pci_bridge_component,
            dvsec=DvsecConfigSpaceOptions(
                device_type=CXL_DEVICE_TYPE.DSP,
                register_locator=DvsecRegisterLocatorOptions(
                    registers=mmio_register.get_dvsec_register_offsets()
                ),
            ),
        )
        self._pci_registers = CxlDownstreamPortConfigSpace(options=pci_registers_options)
        config_space_manager.set_register(self._pci_registers)

    def _get_label(self) -> str:
        if self._is_dummy:
            vcs_str = f"VCS{self._vcs_id}"
            vppb_str = f"vPPB{self._vppb_index}(DSP)"
            return f"{vcs_str}:{vppb_str}"
        return f"DSP{self._port_index}"

    def _create_message(self, message: str) -> str:
        message = f"[{self.__class__.__name__}:{self._get_label()}] {message}"
        return message

    def get_reg_vals(self):
        return self._cxl_io_manager.get_cfg_reg_vals()

    def get_upstream_connection(self) -> CxlConnection:
        return self._upstream_connection

    def set_vppb_index(self, vppb_index: int):
        if self._is_dummy:
            raise Exception("Dummy Downstream Port does not support updating the vPPB index")
        self._vppb_index = vppb_index
        self._pci_bridge_component.set_port_number(self._vppb_index)

    def get_device_type(self) -> CXL_COMPONENT_TYPE:
        return CXL_COMPONENT_TYPE.DSP

    def set_routing_table(self, routing_table: RoutingTable):
        if self._is_dummy:
            raise Exception("Dummy Downstream Port does not support updating the routing table")
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
