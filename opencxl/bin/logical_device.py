"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

import click
import asyncio
from opencxl.util.logger import logger
from typing import List
import humanfriendly
from opencxl.cxl.environment import parse_cxl_environment
from opencxl.apps.single_logical_device import SingleLogicalDevice
from opencxl.apps.multi_logical_device import MultiLogicalDevice
from typing import Union

@click.group(name="ld")
def ld_group():
    """Command group for managing  logical devices."""
    pass


async def run_devices(devices: List[Union[SingleLogicalDevice, MultiLogicalDevice]]):
    try:
        await asyncio.gather(*(device.run() for device in devices))
    except Exception as e:
        logger.error(
            "An error occurred while running the devices.",
            exc_info=e,
        )
    finally:
        await asyncio.gather(*(device.stop() for device in devices))




def start_group(config_file):
    logger.info(f"Starting CXL Logical Device Group - Config: {config_file}")
    cxl_env = parse_cxl_environment(config_file)
    slds = []
    for device_config in cxl_env.single_logical_device_configs:
        sld = SingleLogicalDevice(
            port_index=device_config.port_index,
            memory_size=device_config.memory_size,
            memory_file=device_config.memory_file,
            host=cxl_env.switch_config.host,
            port=cxl_env.switch_config.port,
        )
        slds.append(sld)
    mlds = []
    for device_config in cxl_env.multi_logical_device_configs:
        mld = MultiLogicalDevice(
            num_ld=len(device_config.ld_indexes),
            port_index=device_config.port_index,
            memory_sizes=device_config.memory_size,
            memory_files=device_config.memory_file,
            host=cxl_env.switch_config.host,
            port=cxl_env.switch_config.port
        )
        mlds.append(mld)
    devices = slds + mlds
    asyncio.run(run_devices(devices))


@ld_group.command(name="start")
@click.option("--port", default=1, help="Port number for the service.", show_default=True)
@click.option("--memfile", type=str, default=None, help="Memory file name.")
@click.option("--memsize", type=str, default="256M", help="Memory file size.")
def start(port, memfile, memsize):
    logger.info(f"Starting CXL Single Logical Device at port {port}")
    if memfile is None:
        memfile = f"mem{port}.bin"
    memsize = humanfriendly.parse_size(memsize, binary=True)
    sld = SingleLogicalDevice(port, memsize, memfile)
    mld = MultiLogicalDevice([port], [memsize], [memfile])
    devices = [sld, mld]
    asyncio.run(run_devices(devices))
