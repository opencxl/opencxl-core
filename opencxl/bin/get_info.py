import asyncio
import click

from opencxl.util.logger import logger
from opencxl.apps.fabric_manager import CxlFabricManager
from opencxl.bin import socketio_client
from opencxl.bin.common import BASED_INT


@click.group(name="get-info")
def get_info_group():
    """Command group for component info"""
    pass


@get_info_group.command(name="port")
def get_port():
    asyncio.run(socketio_client.get_port())


@get_info_group.command(name="vcs")
def get_vcs():
    asyncio.run(socketio_client.get_vcs())


@get_info_group.command(name="device")
def get_device():
    asyncio.run(socketio_client.get_device())
