"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

import click
from opencxl.util.logger import logger
from asyncio import gather, run
from typing import List
import humanfriendly
from opencxl.cxl.environment import parse_cxl_environment
from opencxl.apps.single_logical_device import SingleLogicalDeviceClient


async def run_clients(clients: List[SingleLogicalDeviceClient]):
    try:
        await gather(*(client.run() for client in clients))
    except Exception as e:
        logger.error(
            "An error occurred while running the Single Logical Device clients.",
            exc_info=e,
        )
    finally:
        await gather(*(client.stop() for client in clients))


@click.group(name="sld")
def sld_group():
    """Command group for managing single logical devices."""
    pass


def start_group(config_file):
    logger.info(f"Starting CXL Single Logical Device Group - Config: {config_file}")
    cxl_env = parse_cxl_environment(config_file)
    clients = []
    for device_config in cxl_env.single_logical_device_configs:
        client = SingleLogicalDeviceClient(
            port_index=device_config.port_index,
            memory_size=device_config.memory_size,
            memory_file=device_config.memory_file,
            host=cxl_env.switch_config.host,
            port=cxl_env.switch_config.port,
        )
        clients.append(client)
    run(run_clients(clients))


@sld_group.command(name="start")
@click.option("--port", default=1, help="Port number for the service.", show_default=True)
@click.option("--memfile", type=str, default=None, help="Memory file name.")
@click.option("--memsize", type=str, default="256M", help="Memory file size.")
def start(port, memfile, memsize):
    logger.info(f"Starting CXL Single Logical Device at port {port}")
    if memfile is None:
        memfile = f"mem{port}.bin"
    memsize = humanfriendly.parse_size(memsize, binary=True)
    sld_client = SingleLogicalDeviceClient(port, memsize, memfile)
    run(sld_client.run())
