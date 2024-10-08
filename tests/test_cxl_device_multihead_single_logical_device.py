"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from asyncio import gather, create_task
import pytest

from opencxl.apps.multihead_single_logical_device import MultiHeadSingleLogicalDevice
from opencxl.cxl.device.root_port_device import CxlRootPortDevice
from opencxl.cxl.component.cxl_connection import CxlConnection
from opencxl.util.number_const import MB

# Test with 4 ports
num_ports = 4


def test_multihead_single_logical_device():
    memory_size = 256 * MB
    memory_file = "mem.bin"
    transport_connection = CxlConnection()
    MultiHeadSingleLogicalDevice(
        num_ports,
        memory_size=memory_size,
        memory_file=memory_file,
        test_mode=True,
        cxl_connection=transport_connection,
    )


@pytest.mark.asyncio
async def test_multihead_single_logical_device_run_stop(get_gold_std_reg_vals):
    memory_size = 256 * MB
    memory_file = "mem.bin"
    transport_connection = CxlConnection()
    device = MultiHeadSingleLogicalDevice(
        num_ports,
        memory_size=memory_size,
        memory_file=memory_file,
        test_mode=True,
        cxl_connection=transport_connection,
    )

    # check register values after initialization
    # pylint: disable=protected-access
    for sld_device in device._sld_devices:
        reg_vals = str(sld_device._cxl_type3_device.get_reg_vals())
        reg_vals_expected = get_gold_std_reg_vals("SLD")
        assert reg_vals == reg_vals_expected

    async def wait_and_stop():
        await device.wait_for_ready()
        await device.stop()

    tasks = [create_task(device.run()), create_task(wait_and_stop())]
    await gather(*tasks)


@pytest.mark.asyncio
async def test_multihead_single_logical_device_enumeration():
    memory_size = 256 * MB
    memory_file = "mem.bin"
    transport_connection = CxlConnection()
    root_port_device = CxlRootPortDevice(downstream_connection=transport_connection, label="Port0")
    device = MultiHeadSingleLogicalDevice(
        num_ports,
        memory_size=memory_size,
        memory_file=memory_file,
        test_mode=True,
        cxl_connection=transport_connection,
    )
    memory_base_address = 0xFE000000

    async def wait_and_stop():
        await device.wait_for_ready()
        await root_port_device.enumerate(memory_base_address)
        await device.stop()

    tasks = [create_task(device.run()), create_task(wait_and_stop())]
    await gather(*tasks)
