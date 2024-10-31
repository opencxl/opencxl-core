#!/usr/bin/env python

from signal import *
import asyncio
import sys
import asyncio
import glob
import os
import json
from tqdm.auto import tqdm
from random import sample

from opencxl.util.logger import logger
from opencxl.cxl.component.cxl_host import CxlHost
from opencxl.cpu import CPU
from opencxl.util.number_const import MB
from opencxl.cxl.component.cxl_memory_hub import CxlMemoryHub, MEM_ADDR_TYPE
from opencxl.cxl.component.irq_manager import Irq, IrqManager
from opencxl.drivers.cxl_bus_driver import CxlBusDriver
from opencxl.drivers.cxl_mem_driver import CxlMemDriver
from opencxl.drivers.pci_bus_driver import PciBusDriver

host = None

start_tasks = []

train_data_path = None
cpu = None
mem_hub = None


async def my_sys_sw_app(cxl_memory_hub: CxlMemoryHub):
    # Max addr for CFG for 0x9FFFFFFF, given max num bus = 8
    # Therefore, 0xFE000000 for MMIO does not overlap
    global cxl_hpa_base_addr, pci_mmio_base_addr
    pci_cfg_base_addr = 0x10000000
    pci_mmio_base_addr = 0xFE000000
    cxl_hpa_base_addr = 0x100000000000
    sys_mem_base_addr = 0xFFFF888000000000

    # PCI Device
    root_complex = cxl_memory_hub.get_root_complex()
    pci_bus_driver = PciBusDriver(root_complex)
    await pci_bus_driver.init(pci_mmio_base_addr)
    pci_cfg_size = 0x10000000  # assume bus bits n = 8
    for i, device in enumerate(pci_bus_driver.get_devices()):
        cxl_memory_hub.add_mem_range(
            pci_cfg_base_addr + (i * pci_cfg_size), pci_cfg_size, MEM_ADDR_TYPE.CFG
        )
        for bar_info in device.bars:
            if bar_info.base_address == 0:
                continue
            cxl_memory_hub.add_mem_range(bar_info.base_address, bar_info.size, MEM_ADDR_TYPE.MMIO)

    global bar_size
    bar_size = bar_info.size

    # CXL Device
    cxl_bus_driver = CxlBusDriver(pci_bus_driver, root_complex)
    cxl_mem_driver = CxlMemDriver(cxl_bus_driver, root_complex)
    await cxl_bus_driver.init()
    await cxl_mem_driver.init()
    hpa_base = cxl_hpa_base_addr
    for device in cxl_mem_driver.get_devices():
        size = device.get_memory_size()
        successful = await cxl_mem_driver.attach_single_mem_device(device, hpa_base, size)
        if not successful:
            logger.info(f"[SYS-SW] Failed to attach device {device}")
            continue
        # if await device.get_bi_enable():
        cxl_memory_hub.add_mem_range(hpa_base, size, MEM_ADDR_TYPE.CXL_CACHED_BI)
        hpa_base += size

    global accel_mem_size
    accel_mem_size = size

    # System Memory
    sys_mem_size = root_complex.get_sys_mem_size()
    cxl_memory_hub.add_mem_range(sys_mem_base_addr, sys_mem_size, MEM_ADDR_TYPE.DRAM)

    for range in cxl_memory_hub.get_memory_ranges():
        logger.info(
            f"[SYS-SW] MemoryRange: base: 0x{range.base_addr:X}, size: 0x{range.size:X}, type: {str(range.addr_type)}"
        )

    # ranges, irq_handler, dev_count
    global mem_ranges
    mem_ranges = cxl_memory_hub.get_memory_ranges()

    global irq_handler
    irq_handler = host.get_irq_manager()


# pass this from main
# dev_type = CXL_COMPONENT_TYPE.T2
accel_count = 2


def to_dev_mem_addr(dev_id: int, addr: int) -> int:
    # 1. get hpa addr base from mem_ranges
    # assert addr < self._dev_mem_ranges[dev_id][1]
    print(
        f"{cxl_hpa_base_addr:x} {accel_mem_size:x} {dev_id} {addr:x} {(cxl_hpa_base_addr + (dev_id * accel_mem_size) + addr):x}"
    )
    return cxl_hpa_base_addr + (dev_id * accel_mem_size) + addr


def to_dev_mmio_addr(dev_id: int, addr: int) -> int:
    # 1. get mmio addr base from mem_ranges
    # assert addr < self._dev_mmio_ranges[device][1]
    # return self._dev_mmio_ranges[device][0] + addr
    if dev_id == 0:
        base = 0xFE200000
    elif dev_id == 1:
        base = 0xFE400000
    tmp = base + addr
    print(f"{base:x} {addr:x} {tmp:x}")
    return tmp


# params

train_finished_count = 0


async def check_training_finished_type2(_: int):
    global train_finished_count
    train_finished_count += 1
    if train_finished_count == accel_count:
        await do_img_classification_type2()


sample_from_each_category = 5


async def do_img_classification_type2():
    categories = glob.glob(train_data_path + "/val/*")
    global total_samples
    total_samples = len(categories) * sample_from_each_category

    global validation_results
    validation_results = [[] for i in range(total_samples)]

    pic_id = 0
    IMAGE_WRITE_ADDR = 0x8000
    global sampled_file_categories
    sampled_file_categories = []
    for c in categories:
        category_pics = glob.glob(f"{c}/*.JPEG")
        sample_pics = sample(category_pics, sample_from_each_category)
        category_name = c.split(os.path.sep)[-1]
        sampled_file_categories += [category_name] * sample_from_each_category
        for s in sample_pics:
            f = open(s, "rb")
            pic_data = f.read()
            pic_data_int = int.from_bytes(pic_data, "little")
            pic_data_len = len(pic_data)
            pic_data_len_rounded = (((pic_data_len - 1) // 64) + 1) * 64
            for dev_id in range(accel_count):
                event = asyncio.Event()
                write_addr = to_dev_mem_addr(dev_id, IMAGE_WRITE_ADDR)
                print(f"{write_addr:x} pic_data_len:{pic_data_len:x}")
                await store(
                    write_addr,
                    pic_data_len_rounded,
                    pic_data_int,
                    prog_bar=True,
                )
                await cpu.store(to_dev_mmio_addr(dev_id, 0x1810), 8, IMAGE_WRITE_ADDR)
                await cpu.store(to_dev_mmio_addr(dev_id, 0x1818), 8, pic_data_len)
                while True:
                    print(f"waiting...")
                    pic_data_mem_loc_rb = await cpu.load(to_dev_mmio_addr(dev_id, 0x1810), 8)
                    pic_data_len_rb = await cpu.load(to_dev_mmio_addr(dev_id, 0x1818), 8)
                    if pic_data_mem_loc_rb == IMAGE_WRITE_ADDR and pic_data_len_rb == pic_data_len:
                        print(f"really equaled? {pic_data_mem_loc_rb:x} {pic_data_len_rb:x}")
                        break
                    await asyncio.sleep(0.2)

                irq_handler.register_interrupt_handler(
                    Irq.ACCEL_VALIDATION_FINISHED,
                    save_results_type2(pic_id, event),
                    dev_id,
                )
                await irq_handler.send_irq_request(Irq.HOST_SENT, dev_id)
                await event.wait()

                # Currently we don't send the picture information to the device
                # and to prevent race condition, we need to send pics synchronously
            pic_id += 1
            f.close()
    merge_results_type2()
    stop_signal.set()


def save_results_type2(pic_id: int, event: asyncio.Event):
    async def _func(dev_id: int):
        logger.info(f"Saving validation results for pic: {pic_id} from dev: {dev_id}")
        host_result_addr = await cpu.load(to_dev_mmio_addr(dev_id, 0x1820), 8)
        host_result_len = await cpu.load(to_dev_mmio_addr(dev_id, 0x1828), 8)

        host_result_addr = to_dev_mem_addr(dev_id, host_result_addr)
        data_bytes = await load(host_result_addr, host_result_len)
        validate_result = json.loads(data_bytes.decode())
        validation_results[pic_id].append(validate_result)
        event.set()

    return _func


def merge_results_type2():
    correct_count = 0
    for pic_id in range(total_samples):
        merged_result = {}
        max_v = 0
        max_k = 0
        assert len(validation_results[pic_id]) == accel_count
        real_category = sampled_file_categories[pic_id]
        for dev_result in validation_results[pic_id]:
            for k, v in dev_result.items():
                if k not in merged_result:
                    merged_result[k] = v
                else:
                    merged_result[k] += v
                if merged_result[k] > max_v:
                    max_v = merged_result[k]
                    max_k = k
        if max_k == real_category:
            correct_count += 1

        print(f"Picture {pic_id} category: Real: {real_category}, validated: {max_k}")

    print("Validation finished. Results:")
    print(
        f"Correct/Total: {correct_count}/{total_samples} "
        f"({100 * correct_count / total_samples:.2f}%)"
    )


async def load(address: int, size: int, prog_bar: bool = False) -> bytes:
    end = address + size
    result = b""
    with tqdm(
        total=size,
        desc="Reading Data",
        unit="iB",
        unit_scale=True,
        unit_divisor=1024,
        disable=not prog_bar,
    ) as pbar:
        for cacheline_offset in range(address, address + size, 64):
            cacheline = await cpu.load(cacheline_offset, 64)
            chunk_size = min(64, (end - cacheline_offset))
            chunk_data = cacheline.to_bytes(64, "little")
            result += chunk_data[:chunk_size]
            pbar.update(chunk_size)
    return result


async def store(address: int, size: int, value: int, prog_bar: bool = False):
    if address % 64 != 0 or size % 64 != 0:
        raise Exception("Size and address must be aligned to 64!")

    with tqdm(
        total=size,
        desc="Writing Data",
        unit="iB",
        unit_scale=True,
        unit_divisor=1024,
        disable=not prog_bar,
    ) as pbar:
        chunk_count = 0
        while size > 0:
            low_64_byte = value & ((1 << (64 * 8)) - 1)
            print(f"low_64_byte:{low_64_byte:x}")
            await cpu.store(address + (chunk_count * 64), 64, low_64_byte)
            size -= 64
            chunk_count += 1
            value >>= 64 * 8
            pbar.update(64)


async def img_classification_host_app(_cpu: CPU, _mem_hub: CxlMemoryHub):
    global cpu
    cpu = _cpu

    global mem_hub
    mem_hub = _mem_hub

    # Pass init-info mem location to the remote using MMIO
    csv_data_mem_loc = 0x00004000
    csv_data = b""

    logger.info(cpu._create_message("Host main process waiting..."))
    await start_signal.wait()
    logger.info(cpu._create_message("Host main process running!"))

    with open(f"{train_data_path}/noisy_imagenette.csv", "rb") as f:
        csv_data = f.read()
    csv_data_int = int.from_bytes(csv_data, "little")
    csv_data_len = len(csv_data)
    csv_data_len_rounded = (((csv_data_len - 1) // 64) + 1) * 64

    logger.info(cpu._create_message("Storing metadata..."))
    # if dev_type == CXL_COMPONENT_TYPE.T1:
    #     await cpu.store(csv_data_mem_loc, csv_data_len_rounded, csv_data_int, prog_bar=True)
    #     for dev_id in range(accel_count):
    #         await mem_hub.write_mmio(self.to_device_mmio_addr(dev_id, 0x1800), 8, csv_data_mem_loc)
    #         await mem_hub.write_mmio(self.to_device_mmio_addr(dev_id, 0x1808), 8, csv_data_len)
    #         while True:
    #             csv_data_mem_loc_rb = await mem_hub.read_mmio(
    #                 self.to_device_mmio_addr(dev_id, 0x1800), 8
    #             )
    #             csv_data_len_rb = await mem_hub.read_mmio(
    #                 self.to_device_mmio_addr(dev_id, 0x1808), 8
    #             )

    #             if csv_data_mem_loc_rb == csv_data_mem_loc and csv_data_len_rb == csv_data_len:
    #                 break
    #             await asyncio.sleep(0.2)
    #         self._irq_handler.register_interrupt_handler(
    #             Irq.ACCEL_TRAINING_FINISHED, self._host_check_start_validation_type1, dev_id
    #         )
    for dev_id in range(accel_count):
        write_addr = csv_data_mem_loc + to_dev_mem_addr(dev_id, csv_data_mem_loc)

        # TODO: MUST RE-ENABLE
        # await store(write_addr, csv_data_len_rounded, csv_data_int, prog_bar=True)

        await mem_hub.write_mmio(to_dev_mmio_addr(dev_id, 0x1800), 8, write_addr)
        await mem_hub.write_mmio(to_dev_mmio_addr(dev_id, 0x1808), 8, csv_data_len)
        while True:
            csv_data_mem_loc_rb = await mem_hub.read_mmio(to_dev_mmio_addr(dev_id, 0x1800), 8)
            csv_data_len_rb = await mem_hub.read_mmio(to_dev_mmio_addr(dev_id, 0x1808), 8)

            if csv_data_mem_loc_rb == write_addr and csv_data_len_rb == csv_data_len:
                break
            await asyncio.sleep(0.2)

        print(f"dev_id: {dev_id}!!!!")
        irq_handler.register_interrupt_handler(
            Irq.ACCEL_TRAINING_FINISHED, check_training_finished_type2, dev_id
        )

    print("[APP] about to kickoff")
    # kick off accel training
    for dev_id in range(accel_count):
        print(f"irq dev_id: {dev_id}!!!!")
        await irq_handler.send_irq_request(Irq.HOST_READY, dev_id)

    # await self._internal_stop_signal.wait()
    # await stop_signal.wait()
    while True:
        await asyncio.sleep(100000)


async def shutdown(signame=None):
    global host
    global start_tasks
    try:
        stop_tasks = [
            asyncio.create_task(host.stop()),
        ]
        await asyncio.gather(*stop_tasks, return_exceptions=True)
        await asyncio.gather(*start_tasks)
    except Exception as exc:
        print("[HOST]", exc.__traceback__)
    finally:
        os._exit(0)


async def run_demo(signame=None):
    # the other devices are passively running, but the host
    # is responsible for executing the actual demo.

    # replaced by SYS-SW
    # pci_bus_driver = PciBusDriver(host.get_root_complex())
    # cxl_bus_driver = CxlBusDriver(pci_bus_driver, host.get_root_complex())
    # cxl_mem_driver = CxlMemDriver(cxl_bus_driver, host.get_root_complex())

    # await pci_bus_driver.init()
    # await cxl_bus_driver.init()
    # await cxl_mem_driver.init()

    # # hack for demo purposes
    # dev_count = 0
    # next_available_hpa_base = hpa_base
    # for device in cxl_mem_driver.get_devices():
    #     size = device.get_memory_size()
    #     successful = await cxl_mem_driver.attach_single_mem_device(
    #         device, next_available_hpa_base, size
    #     )
    #     if successful:
    #         host.append_dev_mmio_range(
    #             device.pci_device_info.bars[0].base_address, device.pci_device_info.bars[0].size
    #         )
    #         host.append_dev_mem_range(next_available_hpa_base, size)
    #         next_available_hpa_base += size
    #         dev_count += 1

    # host.set_device_count(dev_count)

    # await host.start_job()
    # which is :
    #  self._start_signal.set()
    #  await self._stop_signal.wait()

    start_signal.set()
    await stop_signal.wait()

    # TODO: FIX SIGINT
    # os.kill(os.getppid(), SIGINT)


async def main():
    sw_portno = int(sys.argv[1])
    global train_data_path
    train_data_path = sys.argv[2] if len(sys.argv) > 2 else None

    # app specific sync
    global start_signal
    start_signal = asyncio.Event()

    global stop_signal
    stop_signal = asyncio.Event()

    global host
    global start_tasks
    sw_portno = 22500

    # port_index: int,
    # sys_mem_size: int,
    # sys_sw_app: Callable[[], Awaitable[None]],
    # user_app: Callable[[], Awaitable[None]],
    # host_name: str = None,
    # switch_host: str = "0.0.0.0",
    # switch_port: int = 8000,
    # irq_host: str = "0.0.0.0",
    # irq_port: int = 8500,

    host = CxlHost(
        port_index=0,
        sys_mem_size=(64 * MB),
        sys_sw_app=my_sys_sw_app,
        user_app=img_classification_host_app,
        host_name="ImageClassificationHost",
        # switch_host="0,0,0,0",
        switch_port=sw_portno,
    )
    start_tasks = [
        asyncio.create_task(host.run()),
    ]
    ready_tasks = [
        asyncio.create_task(host.wait_for_ready()),
    ]

    # install signal handlers
    lp = asyncio.get_event_loop()
    lp.add_signal_handler(SIGINT, lambda signame="SIGINT": asyncio.create_task(shutdown(signame)))
    lp.add_signal_handler(SIGIO, lambda signame="SIGIO": asyncio.create_task(run_demo(signame)))

    # host_mem_size = 0x8000  # Needs to be big enough to test cache eviction

    # host_name = "foo"
    # root_port_switch_type = ROOT_PORT_SWITCH_TYPE.PASS_THROUGH
    # memory_controller = RootComplexMemoryControllerConfig(host_mem_size, "foo.bin")
    # root_ports = [RootPortClientConfig(0, "localhost", sw_portno)]
    # memory_ranges = [MemoryRange(MEM_ADDR_TYPE.DRAM, 0x0, host_mem_size)]

    # config = CxlImageClassificationHostConfig(
    #     host_name,
    #     0,
    #     root_port_switch_type,
    #     train_data_path,
    #     memory_controller,
    #     memory_ranges,
    #     root_ports,
    #     coh_type=COH_POLICY_TYPE.DotMemBI,
    #     device_type=CXL_COMPONENT_TYPE.T2,
    #     base_addr=hpa_base,
    # )
    # host = CxlImageClassificationHost(config)

    os.kill(os.getppid(), SIGCONT)

    await asyncio.gather(*ready_tasks)

    await asyncio.Event().wait()  # blocks


if __name__ == "__main__":
    asyncio.run(main())
