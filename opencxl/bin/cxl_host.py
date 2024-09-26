"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

import asyncio
import click
from opencxl.util.logger import logger
from opencxl.cxl.environment import parse_cxl_environment
from opencxl.cxl.component.cxl_component import PORT_TYPE
from opencxl.apps.cxl_host import CxlHost, CxlHostManager, CxlHostUtilClient
from opencxl.bin.common import BASED_INT


@click.group(name="host")
def host_group():
    """Command group for managing CXL Host"""
    pass


@host_group.command(name="reinit")
@click.argument("port", type=BASED_INT)
@click.option("--hpa-base", help="HPA Base Address", type=BASED_INT)
def reinit(port, hpa_base: int):
    client = CxlHostUtilClient()
    try:
        asyncio.run(client.reinit(port, hpa_base))
    except Exception as e:
        logger.info(f"CXL-Host[Port{port}]: {e}")
        return
    logger.info(f"CXL-Host[Port{port}]: Reinit done")


def start(port: int = 0, hm_mode: bool = False):
    logger.info(f"Starting CXL Host on Port{port}")
    host = CxlHost(port_index=port, hm_mode=hm_mode)
    asyncio.run(host.run())


async def run_host_group(host_clients):
    tasks = [asyncio.create_task(host.run()) for host in host_clients]
    await asyncio.gather(*tasks)


def start_group(config_file: str, hm_mode: bool = True):
    logger.info(f"Starting CXL Host Group - Config: {config_file}")
    try:
        environment = parse_cxl_environment(config_file)
    except Exception as e:
        logger.error(f"Failed to parse environment configuration: {e}")
        return

    host_clients = []
    for idx, port_config in enumerate(environment.switch_config.port_configs):
        if port_config.type == PORT_TYPE.USP:
            host_clients.append(CxlHost(port_index=idx, hm_mode=hm_mode))
    asyncio.run(run_host_group(host_clients))


def start_host_manager():
    logger.info(f"Starting CXL HostManager")
    host_manager = CxlHostManager()
    asyncio.run(host_manager.run())
