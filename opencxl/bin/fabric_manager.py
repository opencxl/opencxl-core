"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

import asyncio
import click

from opencxl.util.logger import logger
from opencxl.apps.fabric_manager import CxlFabricManager
from opencxl.bin import socketio_client
from opencxl.bin.common import BASED_INT


# Fabric Manager command group
@click.group(name="fm")
def fabric_manager_group():
    """Command group for Fabric Manager."""
    pass


@fabric_manager_group.command(name="start")
@click.option("--use-test-runner", is_flag=True, help="Run with the test runner.")
def start(use_test_runner):
    """Run the Fabric Manager."""
    logger.info(f"Starting CXL FabricManager")
    fabric_manager = CxlFabricManager(use_test_runner=use_test_runner)
    asyncio.run(fabric_manager.run())


@fabric_manager_group.command(name="bind")
@click.argument("vcs", nargs=1, type=BASED_INT)
@click.argument("vppb", nargs=1, type=BASED_INT)
@click.argument("physical", nargs=1, type=BASED_INT)
@click.argument(
    "ld_id",
    nargs=1,
    type=BASED_INT,
    default=0,
)
def fm_bind(vcs: int, vppb: int, physical: int, ld_id: int):
    asyncio.run(socketio_client.bind(vcs, vppb, physical, ld_id))


@fabric_manager_group.command(name="unbind")
@click.argument("vcs", nargs=1, type=BASED_INT)
@click.argument("vppb", nargs=1, type=BASED_INT)
def fm_unbind(vcs: int, vppb: int):
    asyncio.run(socketio_client.unbind(vcs, vppb))
