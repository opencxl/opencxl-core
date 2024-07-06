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
from opencxl.apps.cxl_complex_host import CxlComplexHost, CxlComplexHostConfig


# class CxlComplexHostConfig:
#     host_name: str
#     root_bus: int
#     root_port_switch_type: ROOT_PORT_SWITCH_TYPE
#     memory_controller: RootComplexMemoryControllerConfig
#     root_ports: List[RootPortClientConfig] = field(default_factory=list)
#     memory_ranges: List[MemoryRange] = field(default_factory=list)


@click.group(name="host")
def host_group():
    """Command group for managing CXL Complex Host"""
    pass


async def run_host_group(hosts):
    tasks = [asyncio.create_task(host.run()) for host in hosts]
    await asyncio.gather(*tasks)


def start_group(config_file: str):
    try:
        environment = parse_cxl_environment(config_file)
    except Exception as e:
        logger.error(f"Failed to parse environment configuration: {e}")
        return

    hosts = []
    for idx, port_config in enumerate(environment.switch_config.port_configs):
        if port_config.type == PORT_TYPE.USP:
            hosts.append(CxlComplexHost(port_index=idx))
    asyncio.run(run_host_group(hosts))
