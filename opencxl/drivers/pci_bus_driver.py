"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from typing import Optional, Tuple, TypedDict
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

BRIDGE_CLASS = PCI_CLASS.BRIDGE << 8 | PCI_BRIDGE_SUBCLASS.PCI_BRIDGE


class PciDeviceInfo(TypedDict):
    vid: int
    did: int
    class_name: str
    bar_addr: int
    bar_range: int
    bdf: int


class PciBusDriver(LabeledComponent):
    def __init__(self, root_complex: RootComplex, label: Optional[str] = None):
        super().__init__(label)
        self._root_complex = root_complex
        self._devices: list[PciDeviceInfo] = []

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

    async def _set_secondary_bus(self, bdf: int, secondary_bus: int):
        bdf_string = bdf_to_string(bdf)
        logger.info(
            self._create_message(f"Setting secondary bus of device {bdf_string} to {secondary_bus}")
        )

        await self._root_complex.write_config(
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

        await self._root_complex.write_config(
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
        await self._root_complex.write_config(
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
        await self._root_complex.write_config(
            bdf,
            REG_ADDR.MEMORY_LIMIT.START,
            REG_ADDR.MEMORY_LIMIT.LEN,
            address_limit_regval,
        )

    async def _set_bar0(self, bdf: int, bar_address: int):
        await self._root_complex.write_config(bdf, BAR_OFFSETS.BAR0, BAR_REGISTER_SIZE, bar_address)

    async def _get_bar0_size(
        self,
        bdf: int,
    ) -> int:
        data = await self._root_complex.read_config(bdf, BAR_OFFSETS.BAR0, BAR_REGISTER_SIZE)
        if data == 0:
            return data
        return 0xFFFFFFFF - data + 1

    async def _read_vid_did(self, bdf: int) -> Optional[tuple[int]]:
        logger.debug(self._create_message(f"Reading VID/DID from {bdf_to_string(bdf)}"))
        vid = await self._root_complex.read_config(
            bdf, REG_ADDR.VENDOR_ID.START, REG_ADDR.VENDOR_ID.LEN
        )
        did = await self._root_complex.read_config(
            bdf, REG_ADDR.DEVICE_ID.START, REG_ADDR.DEVICE_ID.LEN
        )
        logger.debug(self._create_message(f"VID: 0x{vid:x}"))
        logger.debug(self._create_message(f"DID: 0x{did:x}"))
        if did == 0xFFFF and vid == 0xFFFF:
            logger.debug(self._create_message(f"Device not found at {bdf_to_string(bdf)}"))
            return None
        # return (did << 16) | vid
        return vid, did

    async def _read_class_code(self, bdf: int) -> int:
        data = await self._root_complex.read_config(
            bdf, REG_ADDR.CLASS_CODE.START, REG_ADDR.CLASS_CODE.LEN
        )
        if data == 0xFFFF:
            raise Exception("Failed to read class code")
        return data

    async def _read_bar(self, bdf, bar_id) -> int:
        offset = 0x10 + bar_id * 4
        size = 4
        data = await self._root_complex.read_config(bdf, offset, size=size)
        if data == 0xFFFF:
            raise Exception(f"Failed to read bar {bar_id}")
        return data

    async def _read_secondary_bus(self, bdf: int) -> int:
        data = await self._root_complex.read_config(
            bdf,
            REG_ADDR.SECONDARY_BUS_NUMBER.START,
            REG_ADDR.SECONDARY_BUS_NUMBER.LEN,
        )
        if data == 0xFFFF:
            raise Exception("Failed to read secondary bus")
        return data

    async def _read_subordinate_bus(self, bdf: int) -> int:
        data = await self._root_complex.read_config(
            bdf,
            REG_ADDR.SUBORDINATE_BUS_NUMBER.START,
            REG_ADDR.SUBORDINATE_BUS_NUMBER.LEN,
        )
        if data == 0xFFFF:
            raise Exception("Failed to read subordinate bus")
        return data

    async def _read_memory_base(self, bdf: int) -> int:
        data = await self._root_complex.read_config(
            bdf, REG_ADDR.MEMORY_BASE.START, REG_ADDR.MEMORY_BASE.LEN
        )
        if data == 0xFFFF:
            raise Exception("Failed to read memory base")
        return data

    async def _read_memory_limit(self, bdf: int) -> int:
        data = await self._root_complex.read_config(
            bdf, REG_ADDR.MEMORY_LIMIT.START, REG_ADDR.MEMORY_LIMIT.LEN
        )
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
            dev_data = PciDeviceInfo()
            dev_data["bdf"] = bdf
            device_number = extract_device_from_bdf(bdf)
            function_number = extract_function_from_bdf(bdf)

            if function_number != 0 and device_number not in multi_function_devices:
                continue

            vid_did = await self._read_vid_did(bdf)
            if vid_did is None:
                continue
            vid, did = vid_did
            dev_data["vid"] = vid
            dev_data["did"] = did

            is_multifunction = (await self._root_complex.read_config(bdf, 0x0E, 1) & 0x80) >> 7
            if is_multifunction:
                multi_function_devices.add(device_number)

            size = await self._check_bar_size_and_set(bdf, memory_start)
            # NOTE: assume size is less than 0x100000
            if size > 0:
                dev_data["bar_addr"] = memory_start
                dev_data["bar_range"] = 0x100000
                memory_start += 0x100000
            else:
                logger.info(self._create_message(f"BAR0 size of {bdf_to_string(bdf)} is {size}"))

            class_code = await self._read_class_code(bdf)
            if (class_code >> 8) == BRIDGE_CLASS:
                dev_data["class_name"] = "bridge"
                logger.info(
                    self._create_message(
                        f"Found an bridge device at {bdf_to_string(bdf)} (VID: 0x{vid:04x} DID: 0x{did:04x})"
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
                dev_data["class_name"] = "endpoint"
                logger.info(
                    self._create_message(
                        f"Found an endpoint device at {bdf_to_string(bdf)} "
                        f"(VID: 0x{vid:04x} DID: 0x{did:04x})"
                    )
                )
            self._devices.append(dev_data)
        return (bus, memory_start)

    # pylint: enable=duplicate-code
