"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

# pylint: disable=duplicate-code
from asyncio import (
    CancelledError,
    Future,
    Lock,
    create_task,
    current_task,
    gather,
    get_running_loop,
)
from itertools import cycle
import math
from typing import Optional
from dataclasses import dataclass
from opencxl.cxl.component.cxl_cache_manager import CxlCacheManager

from opencxl.cxl.config_space.dvsec.cxl_devices import (
    DvsecCxlCacheableRangeOptions,
    DvsecCxlCapabilityOptions,
)
from opencxl.util.logger import logger
from opencxl.util.component import RunnableComponent
from opencxl.cxl.component.cxl_connection import CxlConnection
from opencxl.cxl.component.cxl_io_manager import CxlIoManager
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
from opencxl.cxl.component.cache_controller import (
    CacheController,
    CacheControllerConfig,
)
from opencxl.cxl.transport.memory_fifo import MemoryFifoPair
from opencxl.cxl.transport.cache_fifo import CacheFifoPair
from opencxl.cxl.transport.transaction import (
    CXL_CACHE_H2DRSP_CACHE_STATE,
    CXL_CACHE_H2DRSP_OPCODE,
    CxlCacheH2DDataPacket,
    CxlCacheH2DRspPacket,
)
from opencxl.cxl.component.device_llc_iogen import (
    DeviceLlcIoGen,
    DeviceLlcIoGenConfig,
)
from opencxl.cxl.component.cxl_cache_dcoh import CxlCacheDcoh
from opencxl.util.number import split_int
from opencxl.util.number_const import KB, MB


@dataclass
class CxlType1DeviceConfig:
    device_name: str
    transport_connection: CxlConnection
    cache_line_count: int = 32
    cache_line_size: int = 64 * KB


class CxlType1Device(RunnableComponent):
    def __init__(
        self,
        config: CxlType1DeviceConfig,
    ):
        self._label = lambda class_name: f"{config.device_name}:{class_name}"
        super().__init__(self._label)

        processor_to_cache_fifo = MemoryFifoPair()
        cache_to_coh_agent_fifo = CacheFifoPair()
        coh_agent_to_cache_fifo = CacheFifoPair()

        self._memory_size = 0
        self._cxl_cache_device_component = None
        self._upstream_connection = config.transport_connection
        self._cache_line_count = config.cache_line_count
        self._cache_line_size = config.cache_line_size
        self._cxl_io_manager = CxlIoManager(
            self._upstream_connection.mmio_fifo,
            None,
            self._upstream_connection.cfg_fifo,
            None,
            device_type=PCI_DEVICE_TYPE.ENDPOINT,
            init_callback=self._init_device,
            label=self._label,
        )
        self._cxl_cache_manager = CxlCacheManager(
            upstream_fifo=self._upstream_connection.cxl_cache_fifo,
            label=self._label,
        )

        self._cxl_memory_device_component = None

        self._cxl_cache_dcoh = CxlCacheDcoh(
            cache_to_coh_agent_fifo=cache_to_coh_agent_fifo,
            coh_agent_to_cache_fifo=coh_agent_to_cache_fifo,
            upstream_fifo=self._upstream_connection.cxl_cache_fifo,
            label=self._label,
        )

        cache_num_assoc = 4
        cache_controller_config = CacheControllerConfig(
            component_name=config.device_name,
            processor_to_cache_fifo=processor_to_cache_fifo,
            cache_to_coh_agent_fifo=cache_to_coh_agent_fifo,
            coh_agent_to_cache_fifo=coh_agent_to_cache_fifo,
            cache_num_assoc=cache_num_assoc,
            cache_num_set=self._cache_line_count // cache_num_assoc,
        )
        self._cache_controller = CacheController(cache_controller_config)

        device_processor_config = DeviceLlcIoGenConfig(
            device_name=config.device_name, processor_to_cache_fifo=processor_to_cache_fifo
        )
        self._device_simple_processor = DeviceLlcIoGen(device_processor_config)

        self._cqid_gen = cycle(range(0, 4096))
        self._cqid_assign_lock = Lock()

    def _init_device(
        self,
        mmio_manager: MmioManager,
        config_space_manager: ConfigSpaceManager,
    ):
        # Create PCiComponent
        pci_identity = PciComponentIdentity(
            vendor_id=EEUM_VID,
            device_id=SW_SLD_DID,
            base_class_code=PCI_CLASS.MEMORY_CONTROLLER,
            sub_class_coce=MEMORY_CONTROLLER_SUBCLASS.CXL_MEMORY_DEVICE,
            programming_interface=0x10,
        )
        pci_component = PciComponent(pci_identity, mmio_manager)

        # Create CxlMemoryDeviceComponent
        logger.debug(f"Total Capacity = {self._memory_size:x}")
        identity = MemoryDeviceIdentity()
        identity.fw_revision = MemoryDeviceIdentity.ascii_str_to_int("EEUM EMU 1.0", 16)
        identity.set_total_capacity(self._memory_size)
        identity.set_volatile_only_capacity(self._memory_size)
        self._cxl_memory_device_component = CxlMemoryDeviceComponent(
            identity,
            decoder_count=0,
            memory_file="",
            label=self._label,
            cache_lines=self._cache_line_count,
            cache_line_size=self._cache_line_size,
        )

        # Create CombinedMmioRegister
        options = CombinedMmioRegiterOptions(cxl_component=self._cxl_memory_device_component)
        mmio_register = CombinedMmioRegister(options=options, parent_name="mmio")

        # Update MmioManager with new bar entires
        mmio_manager.set_bar_entries([BarEntry(register=mmio_register)])
        cache_size_unit = 0
        if self._cache_line_size == 64 * KB:
            cache_size_unit = 0x1
        elif self._cache_line_size == 1 * MB:
            cache_size_unit = 0x2
        else:
            raise Exception("cache_line_size should either be 64KiB or 1MiB")
        # The options can be reused from Type3
        # But maybe we should change its name in the future
        config_space_register_options = CxlType3SldConfigSpaceOptions(
            pci_component=pci_component,
            dvsec=DvsecConfigSpaceOptions(
                register_locator=DvsecRegisterLocatorOptions(
                    registers=mmio_register.get_dvsec_register_offsets()
                ),
                device_type=CXL_DEVICE_TYPE.ACCEL_T1,
                memory_device_component=self._cxl_memory_device_component,
                capability_options=DvsecCxlCapabilityOptions(
                    cache_capable=1,
                    mem_capable=0,
                    hdm_count=0,
                    cache_writeback_and_invalidate_capable=1,
                    cache_size_unit=cache_size_unit,
                    cache_size=self._cache_line_count,
                ),
                # TODO: Use a real range instead of the placeholder range
                cacheable_address_range=DvsecCxlCacheableRangeOptions(0x0, 0xFFFFFFFF0000),
            ),
            doe=CxlDoeExtendedCapabilityOptions(
                cdat_entries=self._cxl_memory_device_component.get_cdat_entries()
            ),
        )
        config_space_register = CxlType3SldConfigSpace(
            options=config_space_register_options, parent_name="cfgspace"
        )

        # ------------------------------
        # Update managers with registers
        # ------------------------------

        # Update ConfigSpaceManager with config space register
        config_space_manager.set_register(config_space_register)

    def get_reg_vals(self):
        return self._cxl_io_manager.get_cfg_reg_vals()

    async def get_next_cqid(self) -> int:
        cqid: int
        async with self._cqid_assign_lock:
            cqid = next(self._cqid_gen)
        return cqid

    async def cxl_cache_readline(self, addr: int, cqid: Optional[int] = None) -> Future[int]:
        loop = get_running_loop()
        fut_data = loop.create_future()  # ugly solution

        def _listen_check_set_fut(pckt: CxlCacheH2DRspPacket | CxlCacheH2DDataPacket):
            # for now, ignore the 32B transfer case.
            if isinstance(pckt, CxlCacheH2DDataPacket):
                # received a data packet
                # since we're ignoring the 32B transfer case,
                # we can just assume this data packet contains everything we want
                fut_data.set_result(pckt.data)
                raise CancelledError
            if (
                pckt.h2drsp_header.cache_opcode != CXL_CACHE_H2DRSP_OPCODE.GO
                or pckt.h2drsp_header.rsp_data
                in (CXL_CACHE_H2DRSP_CACHE_STATE.INVALID, CXL_CACHE_H2DRSP_CACHE_STATE.ERROR)
            ):
                current_task().cancel()  # terminate the listener and free the cqid

        await self._cxl_cache_manager.send_d2h_req_rdown(addr)

        if not cqid:
            cqid = await self.get_next_cqid()

        # avoid sequential cacheline writes by maintaining callbacks which are
        # executed upon retrieval of a Rsp with matching cqid.

        self._cxl_cache_manager.register_cqid_listener(
            cqid=cqid,
            cb=_listen_check_set_fut,
            _timeout=20,
        )

        return fut_data

    async def cxl_cache_writeline(self, addr: int, data: int, cqid: Optional[int] = None):
        def _listen_check_send(pckt: CxlCacheH2DRspPacket):
            """
            Callback registered to listen for the given CQID.
            Checks if the host response packet represents an error.
            If not, sends the requested cacheline write.
            """
            # don't need to perform the type check twice
            # just trust in ourselves!
            if pckt.h2drsp_header.cache_opcode == CXL_CACHE_H2DRSP_OPCODE.GO_ERR_WRITE_PUL:
                current_task().cancel()  # terminate the listener and free the cqid
            self._cxl_cache_manager.send_d2h_data(data, pckt.h2drsp_header.rsp_data)

        await self._cxl_cache_manager.send_d2h_req_itomwr(addr, cqid)

        if not cqid:
            cqid = await self.get_next_cqid()

        # avoid sequential cacheline writes by maintaining callbacks which are
        # executed upon retrieval of a Rsp with matching cqid.
        self._cxl_cache_manager.register_cqid_listener(
            cqid=cqid,
            cb=_listen_check_send,
            _timeout=20,
        )

    async def cxl_cache_readlines(self, addr: int, length: int, parallel: bool = False) -> int:
        # pylint: disable=not-an-iterable
        CACHELINE_LENGTH = 64
        lines = bytearray(max(length, 64))
        if parallel:
            tasks = []
            async for l_idx in range(math.ceil(length / CACHELINE_LENGTH)):
                tasks.append(
                    create_task(
                        self.cxl_cache_readline(
                            addr + l_idx * CACHELINE_LENGTH, await self.get_next_cqid()
                        )
                    )
                )
            await gather(*tasks)
            for l_idx, l_offset in enumerate(range(0, length, CACHELINE_LENGTH)):
                lines[l_offset : l_offset + CACHELINE_LENGTH] = bytes(tasks[l_idx])
        else:
            async for l_idx in range(math.ceil(length / CACHELINE_LENGTH)):
                lines[l_idx * CACHELINE_LENGTH : (l_idx + 1) * CACHELINE_LENGTH] = bytes(
                    await self.cxl_cache_readline(
                        addr + l_idx * CACHELINE_LENGTH, await self.get_next_cqid()
                    )
                )
        return int.from_bytes(lines)

    async def cxl_cache_writelines(
        self, addr: int, data: int, length: int, parallel: bool = False
    ):
        # pylint: disable=not-an-iterable
        CACHELINE_LENGTH = 64
        if parallel:
            tasks = []
        async for l_idx, line in enumerate(split_int(data, length, CACHELINE_LENGTH)):
            write_task = create_task(
                self.cxl_cache_writeline(
                    addr + l_idx * CACHELINE_LENGTH, line, await self.get_next_cqid()
                )
            )
            if parallel:
                tasks.append(write_task)
            else:
                await write_task
        if parallel:
            await gather(*tasks)

    async def _run(self):
        # pylint: disable=duplicate-code
        run_tasks = [
            create_task(self._cxl_io_manager.run()),
            create_task(self._cxl_cache_dcoh.run()),
            create_task(self._cache_controller.run()),
            create_task(self._device_simple_processor.run()),
        ]
        wait_tasks = [
            create_task(self._cxl_io_manager.wait_for_ready()),
            create_task(self._cxl_cache_dcoh.wait_for_ready()),
            create_task(self._cache_controller.wait_for_ready()),
            create_task(self._device_simple_processor.wait_for_ready()),
        ]
        await gather(*wait_tasks)
        await self._change_status_to_running()
        await gather(*run_tasks)

    async def _stop(self):
        # pylint: disable=duplicate-code
        tasks = [
            create_task(self._cxl_io_manager.stop()),
            create_task(self._cxl_cache_dcoh.stop()),
            create_task(self._cache_controller.stop()),
            create_task(self._device_simple_processor.stop()),
        ]
        await gather(*tasks)
