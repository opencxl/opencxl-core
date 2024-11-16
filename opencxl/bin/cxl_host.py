"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

import asyncio
import click
import os

from opencxl.util.logger import logger
from opencxl.cxl.environment import parse_cxl_environment
from opencxl.cxl.component.cxl_component import PORT_TYPE
from opencxl.apps.memory_pooling import run_host
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
    asyncio.run(run_host(port_index=port, irq_port=8500))


async def run_host_group(ports):
    irq_port = 8500
    app_file = "./opencxl/apps/memory_pooling.py"
    for idx in ports:
        args = (str(idx), str(irq_port))
        logger.info(f"{ports} {args}")
        if chld := os.fork() == 0:
            # child process
            try:
                if os.execvp(app_file, (app_file, *args)) == -1:
                    logger.info("EXECVE FAIL!!!")
            except PermissionError as exc:
                raise RuntimeError(f'Failed to invoke "{app_file}" with args {args}') from exc
            except FileNotFoundError as exc:
                raise RuntimeError(f'Couldn\'t find "{app_file}"') from exc
        irq_port += 1


def start_group(config_file: str, hm_mode: bool = True):
    logger.info(f"Starting CXL Host Group - Config: {config_file}")
    try:
        environment = parse_cxl_environment(config_file)
    except Exception as e:
        logger.error(f"Failed to parse environment configuration: {e}")
        return

    ports = []
    for idx, port_config in enumerate(environment.switch_config.port_configs):
        if port_config.type == PORT_TYPE.USP:
            ports.append(idx)
    asyncio.run(run_host_group(ports))


def start_host_manager():
    logger.info(f"Starting CXL HostManager")
    host_manager = CxlHostManager()
    asyncio.run(host_manager.run())
    asyncio.run(host_manager.wait_for_ready())
