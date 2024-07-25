"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from typing import Optional, Tuple
from opencxl.cxl.component.bi_decoder import (
    CxlBIDecoderCapabilityRegister,
    CxlBIRTCapabilityRegister,
)
from opencxl.cxl.mmio.component_register.memcache_register.capability import (
    CAPABILITY_NAME_TO_CAPABILITY_INFO_MAP,
    CxlCapabilityIDToName,
)
from opencxl.util.logger import logger
from opencxl.util.component import LabeledComponent
from opencxl.pci.component.pci import (
    PCI_CLASS,
    PCI_BRIDGE_SUBCLASS,
    memory_base_addr_to_regval,
    memory_limit_addr_to_regval,
)
from opencxl.pci.config_space.pci import REG_ADDR, BAR_OFFSETS, BAR_REGISTER_SIZE
from opencxl.util.pci import (
    extract_device_from_bdf,
    extract_function_from_bdf,
    bdf_to_string,
    generate_bdfs_for_bus,
)
from opencxl.cxl.component.root_complex.root_complex import RootComplex
from opencxl.cxl.device.root_port_device import (
    DeviceEnumerationInfo,
    DvsecRegisterLocator,
    EnumerationInfo,
    PciCapabilities,
    MemoryEnumerationInfo,
)
from opencxl.util.unaligned_bit_structure import BitMaskedBitStructure

BRIDGE_CLASS = PCI_CLASS.BRIDGE << 8 | PCI_BRIDGE_SUBCLASS.PCI_BRIDGE


class PciBusDriver(LabeledComponent):
    def __init__(self, root_complex: RootComplex, label: Optional[str] = None):
        super().__init__(label)
        self._root_complex = root_complex
        self._devices: EnumerationInfo = EnumerationInfo()
        self._fully_scanned = False

    async def init(self):
        await self._scan_pci_devices()
        await self._init_pci_devices()

    async def _scan_pci_devices(self):
        root_bus = self._root_complex.get_root_bus()
        mmio_base_address = self._root_complex.get_mmio_base_address()
        await self._scan_bus(root_bus, mmio_base_address)

    async def _init_pci_devices(self):
        pass

    # pylint: disable=duplicate-code

    async def read_config(self, bdf: int, offset: int, size: int) -> int:
        return await self._root_complex.read_config(bdf, offset, size)

    async def write_config(self, bdf: int, offset: int, size: int, value: int):
        await self._root_complex.write_config(bdf, offset, size, value)

    async def read_mmio(self, address, size) -> int:
        return await self._root_complex.read_mmio(address, size)

    async def write_mmio(self, address, size, value):
        await self._root_complex.write_mmio(address, size, value)

    async def _set_secondary_bus(self, bdf: int, secondary_bus: int):
        bdf_string = bdf_to_string(bdf)
        logger.info(
            self._create_message(f"Setting secondary bus of device {bdf_string} to {secondary_bus}")
        )

        await self.write_config(
            bdf,
            REG_ADDR.SECONDARY_BUS_NUMBER.START,
            REG_ADDR.SECONDARY_BUS_NUMBER.LEN,
            secondary_bus,
        )

    async def _set_subordinate_bus(self, bdf: int, subordinate_bus: int):
        bdf_string = bdf_to_string(bdf)
        logger.info(
            self._create_message(
                f"Setting subordinate bus of device {bdf_string} to {subordinate_bus}"
            )
        )

        await self.write_config(
            bdf,
            REG_ADDR.SUBORDINATE_BUS_NUMBER.START,
            REG_ADDR.SUBORDINATE_BUS_NUMBER.LEN,
            subordinate_bus,
        )

    async def _set_memory_base(self, bdf: int, address_base: int):
        bdf_string = bdf_to_string(bdf)
        logger.info(
            self._create_message(
                f"Setting memory base of device {bdf_string} to {address_base:08x}"
            )
        )
        address_base_regval = memory_base_addr_to_regval(address_base)
        await self.write_config(
            bdf,
            REG_ADDR.MEMORY_BASE.START,
            REG_ADDR.MEMORY_BASE.LEN,
            address_base_regval,
        )

    async def _set_memory_limit(self, bdf: int, address_limit: int):
        logger.info(
            self._create_message(
                f"Setting memory limit of device {bdf_to_string(bdf)} to {address_limit:08x}"
            )
        )
        address_limit_regval = memory_limit_addr_to_regval(address_limit)
        await self.write_config(
            bdf,
            REG_ADDR.MEMORY_LIMIT.START,
            REG_ADDR.MEMORY_LIMIT.LEN,
            address_limit_regval,
        )

    async def _set_bar0(self, bdf: int, bar_address: int):
        await self.write_config(bdf, BAR_OFFSETS.BAR0, BAR_REGISTER_SIZE, bar_address)

    async def _get_bar0_size(
        self,
        bdf: int,
    ) -> int:
        data = await self.read_config(bdf, BAR_OFFSETS.BAR0, BAR_REGISTER_SIZE)
        if data == 0:
            return data
        return 0xFFFFFFFF - data + 1

    async def _read_vid_did(self, bdf: int) -> Optional[int]:
        logger.debug(self._create_message(f"Reading VID/DID from {bdf_to_string(bdf)}"))
        vid = await self.read_config(bdf, REG_ADDR.VENDOR_ID.START, REG_ADDR.VENDOR_ID.LEN)
        did = await self.read_config(bdf, REG_ADDR.DEVICE_ID.START, REG_ADDR.DEVICE_ID.LEN)
        logger.debug(self._create_message(f"VID: 0x{vid:x}"))
        logger.debug(self._create_message(f"DID: 0x{did:x}"))
        if did == 0xFFFF and vid == 0xFFFF:
            logger.debug(self._create_message(f"Device not found at {bdf_to_string(bdf)}"))
            return None
        return (did << 16) | vid

    async def _read_class_code(self, bdf: int) -> int:
        data = await self.read_config(bdf, REG_ADDR.CLASS_CODE.START, REG_ADDR.CLASS_CODE.LEN)
        if data == 0xFFFF:
            raise Exception("Failed to read class code")
        return data

    async def _read_bar(self, bdf, bar_id) -> int:
        offset = 0x10 + bar_id * 4
        size = 4
        data = await self.read_config(bdf, offset, size=size)
        if data == 0xFFFF:
            raise Exception(f"Failed to read bar {bar_id}")
        return data

    async def _read_secondary_bus(self, bdf: int) -> int:
        data = await self.read_config(
            bdf,
            REG_ADDR.SECONDARY_BUS_NUMBER.START,
            REG_ADDR.SECONDARY_BUS_NUMBER.LEN,
        )
        if data == 0xFFFF:
            raise Exception("Failed to read secondary bus")
        return data

    async def _read_subordinate_bus(self, bdf: int) -> int:
        data = await self.read_config(
            bdf,
            REG_ADDR.SUBORDINATE_BUS_NUMBER.START,
            REG_ADDR.SUBORDINATE_BUS_NUMBER.LEN,
        )
        if data == 0xFFFF:
            raise Exception("Failed to read subordinate bus")
        return data

    async def _read_memory_base(self, bdf: int) -> int:
        data = await self.read_config(bdf, REG_ADDR.MEMORY_BASE.START, REG_ADDR.MEMORY_BASE.LEN)
        if data == 0xFFFF:
            raise Exception("Failed to read memory base")
        return data

    async def _read_memory_limit(self, bdf: int) -> int:
        data = await self.read_config(bdf, REG_ADDR.MEMORY_LIMIT.START, REG_ADDR.MEMORY_LIMIT.LEN)
        if data == 0xFFFF:
            raise Exception("Failed to read memory limit")
        return data

    async def _check_bar_size_and_set(self, bdf: int, memory_base: int):
        bdf_string = bdf_to_string(bdf)
        logger.info(self._create_message(f"Checking BAR0 size of device {bdf_string}"))

        # NOTE: Write 0xFFFFFFFF to BAR0 to get the size of BAR0
        await self._set_bar0(bdf, 0xFFFFFFFF)
        size = await self._get_bar0_size(bdf)
        logger.info(self._create_message(f"BAR0 size of device {bdf_string} is {size}"))
        if size > 0:
            logger.info(
                self._create_message(
                    f"Setting BAR0 address of device {bdf_string} to 0x{memory_base:08x}"
                )
            )
            await self._set_bar0(bdf, memory_base)
        else:
            await self._set_bar0(bdf, 0)
        return size

    async def _scan_bus(self, bus: int, memory_start: int) -> Tuple[int, int]:
        logger.debug(self._create_message(f"Scanning PCI Bus {bus}"))
        bdf_list = generate_bdfs_for_bus(bus)
        multi_function_devices = set()

        for bdf in bdf_list:
            dev_enum_info = DeviceEnumerationInfo()
            dev_enum_info.bdf = bdf
            device_number = extract_device_from_bdf(bdf)
            function_number = extract_function_from_bdf(bdf)

            if function_number != 0 and device_number not in multi_function_devices:
                continue

            vid_did = await self._read_vid_did(bdf)
            if vid_did is None:
                continue
            dev_enum_info.vid_did = vid_did

            is_multifunction = (await self.read_config(bdf, 0x0E, 1) & 0x80) >> 7
            if is_multifunction:
                multi_function_devices.add(device_number)

            size = await self._check_bar_size_and_set(bdf, memory_start)
            # NOTE: assume size is less than 0x100000
            if size > 0:
                bar0 = await self._read_bar(bdf, 0)
                dev_enum_info.bars.append(bar0)
                dev_enum_info.mmio_range = MemoryEnumerationInfo(
                    memory_base=memory_start, memory_limit=memory_start + 0x100000 - 1
                )
                memory_start += 0x100000
            else:
                logger.info(self._create_message(f"BAR0 size of {bdf_to_string(bdf)} is {size}"))

            class_code = await self._read_class_code(bdf)
            if (class_code >> 8) == BRIDGE_CLASS:
                dev_enum_info.is_bridge = True
                logger.info(
                    self._create_message(
                        f"Found a bridge device at {bdf_to_string(bdf)} (VID/DID:{vid_did:08x})"
                    )
                )

                await self._set_secondary_bus(bdf, bus + 1)
                await self._set_subordinate_bus(bdf, 0xFF)

                (bus, memory_end) = await self._scan_bus(bus + 1, memory_start)
                if memory_start != memory_end:
                    await self._set_memory_base(bdf, memory_start)
                    await self._set_memory_limit(bdf, memory_end - 1)
                memory_start = memory_end
                await self._set_subordinate_bus(bdf, bus)
            else:
                dev_enum_info.is_bridge = False
                logger.info(
                    self._create_message(
                        f"Found an endpoint device at {bdf_to_string(bdf)} "
                        f"(VID/DID:{vid_did:08x})"
                    )
                )

            self._devices.devices.append(dev_enum_info)
        return (bus, memory_start)

    async def scan_dvsec_register_locator(
        self, bdf: int, cap_offset: int, length: int, capabilities: PciCapabilities
    ):
        logger.info(self._create_message("Found Register Locator DVSEC"))
        block_offset_base = 0x0C
        blocks = int((length - block_offset_base) / 8)
        block_size = 8
        for block_index in range(blocks):
            block_offset = block_offset_base + block_index * block_size

            register_offset_low = await self.read_config(bdf, cap_offset + block_offset, 4)
            if register_offset_low is None:
                raise Exception(
                    f"Failed to read Register Block {block_index + 1} - Register Offset Low"
                )
            register_offset_high = await self.read_config(bdf, cap_offset + block_offset + 4, 4)
            if register_offset_high is None:
                raise Exception(
                    f"Failed to read Register Block {block_index + 1} - Register Offset High"
                )

            register_bir = register_offset_low & 0x7
            register_block_identifier = (register_offset_low >> 8) & 0xFF
            bar_offset = (register_offset_low & 0xFFFF0000) | register_offset_high << 32

            if register_block_identifier == 0x01:
                component_registers = DvsecRegisterLocator()
                capabilities.dvsec.register_locators.component_registers = component_registers
                component_registers.bar = register_bir
                component_registers.offset = bar_offset
                logger.info(
                    self._create_message(
                        f"(Block {block_index + 1}) "
                        f"Component Registers, BAR: {component_registers.bar}"
                    )
                )
                logger.info(
                    self._create_message(
                        f"(Block {block_index + 1}) "
                        f"Component Registers, OFFSET: {component_registers.offset}"
                    )
                )
            elif register_block_identifier == 0x03:
                cxl_device_registers = DvsecRegisterLocator()
                capabilities.dvsec.register_locators.cxl_device_registers = cxl_device_registers
                cxl_device_registers.bar = register_bir
                cxl_device_registers.offset = bar_offset
                logger.info(
                    self._create_message(
                        f"(Block {block_index + 1}) CXL Device Registers, "
                        f"BAR: {cxl_device_registers.bar}"
                    )
                )
                logger.info(
                    self._create_message(
                        f"(Block {block_index + 1}) CXL Device Registers, "
                        f"OFFSET: {cxl_device_registers.offset}"
                    )
                )

    async def scan_dvsec(self, info: DeviceEnumerationInfo, cap_offset: int):
        bdf: int = info.bdf
        capabilities: PciCapabilities = info.capabilities
        dvsec_header1 = await self.read_config(bdf, cap_offset + 0x04, 4)
        if dvsec_header1 is None:
            raise Exception("Failed to read DVSEC Header 1")
        dvsec_header2 = await self.read_config(bdf, cap_offset + 0x08, 2)
        if dvsec_header2 is None:
            raise Exception("Failed to read DVSEC Header 2")

        vendor_id = dvsec_header1 & 0xFFFF
        revision_id = (dvsec_header1 >> 16) & 0xF
        length = (dvsec_header1 >> 20) & 0xFFF
        dvsec_id = dvsec_header2

        if vendor_id == 0x1E98 and revision_id == 0x0 and dvsec_id == 0x0008:
            await self.scan_dvsec_register_locator(bdf, cap_offset, length, capabilities)
        elif vendor_id == 0x1E98 and dvsec_id == 0x0000:
            assert length == 0x03C
            print(f"vendor: {vendor_id}, revision: {revision_id}, dvsec: {dvsec_id}")
            print("GOT DVSEC HEADER")
            cxl_capability = await self.read_config(bdf, cap_offset + 0x0A, 2)
            mem_capable = (cxl_capability & 0x4) >> 2
            hdm_count = (cxl_capability & 0x30) >> 4
            if mem_capable:
                if hdm_count == 0b01 or hdm_count == 0b10:
                    # 1 or 2 hdm range(s)
                    # TODO: check valid bit for the range
                    info.dev_cxl_mem_enable = True
                    range1_size_high = await self.read_config(bdf, cap_offset + 0x18, 4)
                    print(f"High: {range1_size_high}")
                    range1_size_low = await self.read_config(bdf, cap_offset + 0x1C, 4)
                    print(f"Low: {range1_size_low}")
                    info.dev_mem_range1_size = (range1_size_high << 32) | (
                        range1_size_low & 0xF0000000
                    )
                    range1_base_high = await self.read_config(bdf, cap_offset + 0x20, 4)
                    range1_base_low = await self.read_config(bdf, cap_offset + 0x24, 4)
                    info.dev_mem_range1_base = (range1_base_high << 32) | (
                        range1_base_low & 0xF0000000
                    )
                    print(
                        f"Range 1 base: {info.dev_mem_range1_base}, "
                        f"range 1 size: {info.dev_mem_range1_size}"
                    )

                    if hdm_count == 0b10:
                        # 2 hdm ranges
                        range2_size_high = await self.read_config(bdf, cap_offset + 0x28, 4)
                        range2_size_low = await self.read_config(bdf, cap_offset + 0x2C, 4)
                        info.dev_mem_range2_size = (range2_size_high << 32) | (
                            range2_size_low & 0xF0000000
                        )

                        range2_base_high = await self.read_config(bdf, cap_offset + 0x30, 4)
                        range2_base_low = await self.read_config(bdf, cap_offset + 0x34, 4)
                        info.dev_mem_range2_base = (range2_base_high << 32) | (
                            range2_base_low & 0xF0000000
                        )
                        print(
                            f"Range 2 base: {info.dev_mem_range2_base}, "
                            f"range 2 size: {info.dev_mem_range2_size}"
                        )
                else:
                    raise Exception(
                        f"Illegal hdm_count 0b{hdm_count:02b} for mem_capable 0b{mem_capable:1b}"
                    )

    async def scan_pcie_cap_helper(self, info: DeviceEnumerationInfo, offset: int):
        bdf = info.bdf
        data = await self.read_config(bdf, offset, 4)
        if data is None:
            return

        cap_id = data & 0xFFFF
        cap_version = (data >> 16) & 0xF
        next_cap_offset = (data >> 20) & 0xFFF

        is_dvsec_cap = cap_id == 0x0023 and cap_version == 0x1
        if is_dvsec_cap:
            await self.scan_dvsec(info, offset)

        if next_cap_offset != 0:
            await self.scan_pcie_cap_helper(info, next_cap_offset)

    async def scan_pci_capabilities(self, info: DeviceEnumerationInfo):
        PCIE_CONFIG_BASE = 0x100
        await self.scan_pcie_cap_helper(info, PCIE_CONFIG_BASE)

    async def write_cachemem_register(self, offset: int, value: int, len: int):
        await self.write_mmio(offset, len, value)

    async def scan_component_registers(self, info: DeviceEnumerationInfo):
        component_registers = info.capabilities.dvsec.register_locators.component_registers
        if not component_registers:
            return

        component_register_bar = component_registers.bar
        component_register_offset = component_registers.offset
        mmio_base = info.bars[component_register_bar]
        cxl_cachemem_offset = mmio_base + component_register_offset + 0x1000

        logger.info(
            self._create_message(f"Scanning Component Registers at 0x{cxl_cachemem_offset:x}")
        )

        cxl_capability_header = await self.read_mmio(cxl_cachemem_offset, 4)
        cxl_capability_id = cxl_capability_header & 0xFFFF
        cxl_capability_version = (cxl_capability_header >> 16) & 0xF
        cxl_cachemem_version = (cxl_capability_header >> 20) & 0xF
        array_size = (cxl_capability_header >> 24) & 0xFF

        logger.debug(self._create_message(f"cxl_capability_id: {cxl_capability_id:x}"))
        logger.debug(self._create_message(f"cxl_capability_version: {cxl_capability_version:x}"))
        logger.debug(self._create_message(f"cxl_cachemem_version: {cxl_cachemem_version:x}"))
        logger.debug(self._create_message(f"array_size: {array_size:x}"))

        if cxl_capability_id != 0x0001:
            return

        logger.info(self._create_message("Found Component Registers"))
        info.component_registers["hdm_decoder"] = 0
        for header_index in range(array_size):
            header_offset = header_index * 4 + 4 + cxl_cachemem_offset
            header_info = await self.read_mmio(header_offset, 4)
            cxl_capability_id = header_info & 0xFFFF
            cxl_capability_version = (header_info >> 16) & 0xF
            offset = (header_info >> 20) & 0xFFF
            logger.info(
                self._create_message(
                    f"Found {CxlCapabilityIDToName.get(cxl_capability_id)} Capability Header"
                )
            )
            info.component_registers[CxlCapabilityIDToName.get_original_name(cxl_capability_id)] = (
                cxl_cachemem_offset + offset
            )

    async def find_register_offset_by_name(self, bar: int, name: str) -> int:
        await self.init_dvsec_and_capability()
        all_devices = self._devices.devices
        info = None
        for dev in all_devices:
            if bar in dev.bars:
                info = dev
        if info is None:
            raise Exception(f"{bar} is not valid!")

        if name not in CAPABILITY_NAME_TO_CAPABILITY_INFO_MAP:
            raise Exception(f"{name} is not a valid capability!")

        offset = info.component_registers[name]
        if offset is None:
            raise Exception(f"Capability {name} does not exist for this device!")
        return offset

    async def write_register_by_name(
        self, bar: int, reg: BitMaskedBitStructure, name: str, len: int, inner_offset: int = 0
    ):
        """
        Writes to a register by name.
        inner_offset is used for the offset of the register within that capability structure
        e.g., BI RT Capability: 0x00, BI RT Control: 0x04, ...
        """
        location = await self.find_register_offset_by_name(bar, name)
        await self.write_cachemem_register(
            location + inner_offset, reg.read_bytes(0x0, len - 1), len
        )

    async def read_register_by_name(
        self, bar: int, name: str, len: int, inner_offset: int = 0
    ) -> int:
        location = await self.find_register_offset_by_name(bar, name)
        return await self.read_mmio(location + inner_offset, len)

    async def write_bi_rt_capability(self, bar: int, reg: CxlBIRTCapabilityRegister, len: int = 4):
        await self.write_register_by_name(bar, reg, "bi_route_table", len)

    async def write_bi_decoder_capability(
        self, bar: int, reg: CxlBIDecoderCapabilityRegister, len: int = 4
    ):
        await self.write_register_by_name(bar, reg, "bi_decoder", len)

    async def set_up_router_hdm_decoder(self, info: DeviceEnumerationInfo):
        pass

    async def get_dev_mem_size(self, info: DeviceEnumerationInfo):
        await self.init_dvsec_and_capability()
        dvsec_registers = info.capabilities.dvsec.register_locators.cxl_device_registers
        if not dvsec_registers:
            return
        addr = dvsec_registers.bar + dvsec_registers.offset + 0x1000
        data = await self.read_mmio(addr + 4, 4)
        print(f"Data: {data:08x}")

    async def init_dvsec_and_capability(self):
        all_devices = self._devices.get_all_devices()
        if not self._fully_scanned:
            self._fully_scanned = True
            for info in all_devices:
                await self.scan_pci_capabilities(info)
                await self.scan_component_registers(info)
                print(info)

    # pylint: enable=duplicate-code
