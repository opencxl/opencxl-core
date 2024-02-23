"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from asyncio import create_task, gather
from dataclasses import dataclass
from typing import Optional

from opencxl.util.logger import logger
from opencxl.util.component import RunnableComponent
from opencxl.cxl.component.cxl_connection import CxlConnection
from opencxl.cxl.component.cxl_mem_manager import CxlMemManager
from opencxl.cxl.mmio import CombinedMmioRegister, CombinedMmioRegiterOptions
from opencxl.cxl.config_space.dvsec import (
    CXL_DEVICE_TYPE,
    DvsecConfigSpaceOptions,
    DvsecRegisterLocatorOptions,
)
from opencxl.cxl.config_space.doe.doe import CxlDoeExtendedCapabilityOptions
from opencxl.cxl.config_space.device import (
    CxlType3SldConfigSpace,
    CxlType3SldConfigSpaceOptions,
)
from opencxl.cxl.component.cxl_memory_device_component import (
    CxlMemoryDeviceComponent,
    MemoryDeviceIdentity,
    HDM_DECODER_COUNT,
)
from opencxl.pci.component.pci import (
    PciComponent,
    PciComponentIdentity,
    EEUM_VID,
    SW_SLD_DID,
    PCI_CLASS,
    MEMORY_CONTROLLER_SUBCLASS,
)
from opencxl.pci.component.mmio_manager import MmioManager, BarEntry
from opencxl.pci.component.config_space_manager import (
    ConfigSpaceManager,
    PCI_DEVICE_TYPE,
)


@dataclass
class SingleLogicalDeviceConfig:
    serial_number: str
    port_index: int
    memory_size: int  # in bytes
    memory_file: str
    vendor_id: int = EEUM_VID
    device_id: int = SW_SLD_DID
    subsystem_vendor_id: int = 0
    subsystem_id: int = 0


class SingleLogicalDevice(RunnableComponent):
    def __init__(
        self,
        transport_connection: CxlConnection,
        memory_size: int,
        memory_file: str,
        decoder_count: HDM_DECODER_COUNT = HDM_DECODER_COUNT.DECODER_4,
        label: Optional[str] = None,
    ):
        super().__init__(label)
        self._upstream_connection = transport_connection

        # -----------------------------------------------------------------
        # Create Managers: ConfigSpaceManager / MmioManager / CxlMemManager
        # -----------------------------------------------------------------

        # Create ConfigSpaceManager
        self._config_space_manager = ConfigSpaceManager(
            upstream_fifo=self._upstream_connection.cfg_fifo,
            label=self._label,
            device_type=PCI_DEVICE_TYPE.ENDPOINT,
        )

        # Create MmioManager
        self._mmio_manager = MmioManager(
            upstream_fifo=self._upstream_connection.mmio_fifo, label=self._label
        )

        # Create CxlMemManager
        self._cxl_mem_manager = CxlMemManager(
            upstream_fifo=self._upstream_connection.cxl_mem_fifo, label=self._label
        )

        # ----------------------------------------------------------
        # Create Components: CxlMemoryDeviceComponent / PciComponent
        # ----------------------------------------------------------

        # Create CxlMemoryDeviceComponent
        logger.debug(f"Total Capacity = {memory_size:x}")
        identity = MemoryDeviceIdentity()
        identity.fw_revision = MemoryDeviceIdentity.ascii_str_to_int("EEUM EMU 1.0", 16)
        identity.set_total_capacity(memory_size)
        identity.set_volatile_only_capacity(memory_size)
        cxl_memory_device_component = CxlMemoryDeviceComponent(
            identity,
            decoder_count=decoder_count,
            memory_file=memory_file,
            label=label,
        )

        # Create PCiComponent
        pci_identity = PciComponentIdentity(
            vendor_id=EEUM_VID,
            device_id=SW_SLD_DID,
            base_class_code=PCI_CLASS.MEMORY_CONTROLLER,
            sub_class_coce=MEMORY_CONTROLLER_SUBCLASS.CXL_MEMORY_DEVICE,
            programming_interface=0x10,
        )
        pci_component = PciComponent(pci_identity, self._mmio_manager)

        # ---------------------------------------------------------------
        # Create Registers: CombinedMmioRegister / CxlType3SldConfigSpace
        # ---------------------------------------------------------------

        # Create CombinedMmioRegister
        options = CombinedMmioRegiterOptions(cxl_component=cxl_memory_device_component)
        mmio_register = CombinedMmioRegister(options=options, parent_name="mmio")

        # Update MmioManager with new bar entires
        self._mmio_manager.set_bar_entries([BarEntry(register=mmio_register)])

        config_space_register_options = CxlType3SldConfigSpaceOptions(
            pci_component=pci_component,
            dvsec=DvsecConfigSpaceOptions(
                register_locator=DvsecRegisterLocatorOptions(
                    registers=mmio_register.get_dvsec_register_offsets()
                ),
                device_type=CXL_DEVICE_TYPE.LD,
                memory_device_component=cxl_memory_device_component,
            ),
            doe=CxlDoeExtendedCapabilityOptions(
                cdat_entries=cxl_memory_device_component.get_cdat_entries()
            ),
        )

        # Create CxlType3SldConfigSpace
        config_space_register = CxlType3SldConfigSpace(
            options=config_space_register_options, parent_name="cfgspace"
        )

        # ------------------------------
        # Update managers with registers
        # ------------------------------

        # Update ConfigSpaceManager with config space register
        self._config_space_manager.set_register(config_space_register)

        # Update CxlMemManager with a CxlMemoryDeviceComponent
        self._cxl_mem_manager.set_memory_device_component(cxl_memory_device_component)

    def get_reg_vals(self):
        return self._config_space_manager.get_register()

    async def _run(self):
        # pylint: disable=duplicate-code
        tasks = [
            create_task(self._mmio_manager.run()),
            create_task(self._config_space_manager.run()),
            create_task(self._cxl_mem_manager.run()),
        ]
        await self._change_status_to_running()
        await gather(*tasks)

    async def _stop(self):
        # pylint: disable=duplicate-code
        tasks = [
            create_task(self._mmio_manager.stop()),
            create_task(self._config_space_manager.stop()),
            create_task(self._cxl_mem_manager.stop()),
        ]
        await gather(*tasks)
