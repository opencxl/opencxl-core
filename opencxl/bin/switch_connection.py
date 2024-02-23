"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from opencxl.util.logger import logger
import asyncio
from opencxl.cxl.component.switch_connection_manager import (
    SwitchConnectionManager,
)


def exception_callback(loop, context):
    exception = context.get("exception")

    if exception:
        logger.error(f"Caught exception: {exception}")


async def main():
    # Set up an exception handler
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(exception_callback)
    await manager.run()


if __name__ == "__main__":
    logger.info("Starting SwitchConnectionManager")
    logger.create_log_file(
        "logs/switch_connection.log",
        loglevel="DEBUG",
        show_timestamp=True,
        show_loglevel=True,
        show_linenumber=True,
    )
    manager = SwitchConnectionManager(8)

    asyncio.run(main())
