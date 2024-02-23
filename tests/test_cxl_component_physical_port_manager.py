"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from asyncio import create_task, gather
import pytest

from opencxl.cxl.component.physical_port_manager import (
    PhysicalPortManager,
    PortConfig,
    PORT_TYPE,
    UpstreamPortDevice,
    DownstreamPortDevice,
)
from opencxl.cxl.component.switch_connection_manager import SwitchConnectionManager


BASE_TEST_PORT = 9000


def test_physical_port_manager_init(get_gold_std_reg_vals):
    # pylint: disable=duplicate-code
    # CE-94
    port_configs = [
        PortConfig(PORT_TYPE.USP),
        PortConfig(PORT_TYPE.USP),
        PortConfig(PORT_TYPE.DSP),
        PortConfig(PORT_TYPE.DSP),
    ]
    port = BASE_TEST_PORT + pytest.PORT.TEST_1
    switch_connection_manager = SwitchConnectionManager(port_configs, port=port)
    physical_port_manager = PhysicalPortManager(
        switch_connection_manager=switch_connection_manager, port_configs=port_configs
    )
    for port_index, port_config in enumerate(port_configs):
        port_device = physical_port_manager.get_port_device(port_index)
        reg_vals = str(port_device.get_reg_vals())
        if port_config.type == PORT_TYPE.USP:
            assert isinstance(port_device, UpstreamPortDevice)
            reg_vals_expected = get_gold_std_reg_vals("USP")
            assert reg_vals == reg_vals_expected
        else:
            assert isinstance(port_device, DownstreamPortDevice)
            reg_vals_expected = get_gold_std_reg_vals("DSP")
            assert reg_vals == reg_vals_expected

    with pytest.raises(Exception):
        physical_port_manager.get_port_device(len(port_configs))
    assert physical_port_manager.get_port_counts() == len(port_configs)


@pytest.mark.asyncio
async def test_physical_port_manager_run_and_stop():
    # pylint: disable=duplicate-code
    port_configs = [
        PortConfig(PORT_TYPE.USP),
        PortConfig(PORT_TYPE.USP),
        PortConfig(PORT_TYPE.DSP),
        PortConfig(PORT_TYPE.DSP),
    ]
    port = BASE_TEST_PORT + pytest.PORT.TEST_2
    switch_connection_manager = SwitchConnectionManager(port_configs, port=port)
    physical_port_manager = PhysicalPortManager(
        switch_connection_manager=switch_connection_manager, port_configs=port_configs
    )

    async def wait_and_stop():
        await physical_port_manager.wait_for_ready()
        await physical_port_manager.stop()

    tasks = [create_task(wait_and_stop()), create_task(physical_port_manager.run())]
    await gather(*tasks)


@pytest.mark.asyncio
async def test_physical_port_manager_stop_before_run():
    # pylint: disable=duplicate-code
    port_configs = [
        PortConfig(PORT_TYPE.USP),
        PortConfig(PORT_TYPE.USP),
        PortConfig(PORT_TYPE.DSP),
        PortConfig(PORT_TYPE.DSP),
    ]
    port = BASE_TEST_PORT + pytest.PORT.TEST_3
    switch_connection_manager = SwitchConnectionManager(port_configs, port=port)
    physical_port_manager = PhysicalPortManager(
        switch_connection_manager=switch_connection_manager, port_configs=port_configs
    )

    with pytest.raises(Exception, match="Cannot stop when it is not running"):
        await physical_port_manager.stop()


@pytest.mark.asyncio
async def test_physical_port_manager_run_after_run():
    # pylint: disable=duplicate-code
    port_configs = [
        PortConfig(PORT_TYPE.USP),
        PortConfig(PORT_TYPE.USP),
        PortConfig(PORT_TYPE.DSP),
        PortConfig(PORT_TYPE.DSP),
    ]
    port = BASE_TEST_PORT + pytest.PORT.TEST_4
    switch_connection_manager = SwitchConnectionManager(port_configs, port=port)
    physical_port_manager = PhysicalPortManager(
        switch_connection_manager=switch_connection_manager, port_configs=port_configs
    )

    async def wait_and_run():
        await physical_port_manager.wait_for_ready()
        with pytest.raises(Exception, match="Cannot run when it is not stopped"):
            await physical_port_manager.run()
        await physical_port_manager.stop()

    tasks = [create_task(physical_port_manager.run()), create_task(wait_and_run())]
    await gather(*tasks)
