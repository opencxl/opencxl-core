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
from opencxl.cxl.component.root_complex.root_complex import RootComplexMemoryControllerConfig
from opencxl.cxl.component.root_complex.root_port_client_manager import RootPortClientConfig
from opencxl.apps.cxl_complex_host import CxlComplexHost, CxlComplexHostConfig


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
    for _, port_config in enumerate(environment.switch_config.port_configs):
        if port_config.type == PORT_TYPE.USP:
            # TODO: Placeholder config for test purpose,
            # This doesn't even run. MUST be changed
            host_name = "foo"
            root_bus = 1
            root_port_switch_type = 1
            memory_controller = RootComplexMemoryControllerConfig(2000, "foo.bin")
            root_ports = [RootPortClientConfig(0, "localhost", 8000)]
            memory_ranges = [1, 2]

            config = CxlComplexHostConfig(
                host_name,
                root_bus,
                root_port_switch_type,
                memory_controller,
                root_ports,
                memory_ranges,
            )
            hosts.append(CxlComplexHost(config))
    asyncio.run(run_host_group(hosts))
