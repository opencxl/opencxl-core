"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

import asyncio
import click
from opencxl.util.logger import logger
from opencxl.apps.fabric_manager import CxlFabricManager


# Fabric Manager command group
@click.group(name="fm")
def fabric_manager_group():
    """Command group for Fabric Manager."""
    pass


@fabric_manager_group.command(name="start")
@click.option("--use-test-runner", is_flag=True, help="Run with the test runner.")
def start(use_test_runner):
    """Run the Fabric Manager."""
    logger.create_log_file(
        "logs/fabric_manager.log",
        loglevel="DEBUG",
        show_timestamp=True,
        show_loglevel=True,
        show_linenumber=False,
    )
    fabric_manager = CxlFabricManager(use_test_runner=use_test_runner)
    asyncio.run(fabric_manager.run())
