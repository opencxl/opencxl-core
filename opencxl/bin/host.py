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
from opencxl.apps.host import HostClient


async def run_host_group(host_clients):
    tasks = [asyncio.create_task(host.run()) for host in host_clients]
    await asyncio.gather(*tasks)


@click.group(name="host")
def host_group():
    """Command group for managing CXL host configurations."""
    pass


@host_group.command(name="start-group")
@click.argument("config_file", type=click.Path(exists=True))
def start_group(config_file):
    logger.info(f"Starting CXL Host Group with configuration from: {config_file}")
    logger.create_log_file(
        "logs/host_group.log",
        loglevel="DEBUG",
        show_timestamp=True,
        show_loglevel=True,
        show_linenumber=False,
    )

    try:
        environment = parse_cxl_environment(config_file)
    except Exception as e:
        logger.error(f"Failed to parse environment configuration: {e}")
        return

    host_clients = []
    for idx, port_config in enumerate(environment.switch_config.port_configs):
        if port_config.type == PORT_TYPE.USP:
            host_clients.append(HostClient(port_index=idx))

    asyncio.run(run_host_group(host_clients))


@host_group.command(name="start")
@click.option("--port", default=0, help="Port number for the service.", show_default=True)
def start(port):
    logger.info(f"Starting CXL Host Simulator at port {port}")
    logger.create_log_file(
        f"logs/host_port{port}.log",
        loglevel="DEBUG",
        show_timestamp=True,
        show_loglevel=True,
        show_linenumber=False,
    )

    host = HostClient(port)
    asyncio.run(host.run())
