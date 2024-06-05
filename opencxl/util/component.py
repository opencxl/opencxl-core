"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from enum import Enum, auto
from abc import ABC, abstractmethod
from asyncio import create_task, gather, Condition
from typing import Optional
from opencxl.util.logger import logger
import traceback

class COMPONENT_STATUS(Enum):
    STOPPED = auto()
    STARTING = auto()
    RUNNING = auto()
    STOPPING = auto()


class LabeledComponent:
    def __init__(self, label: Optional[str] = None):
        self._label = label

    def _create_message(self, message):
        if self._label:
            message = f"[{self.__class__.__name__}:{self._label}] {message}"
        else:
            message += f"[{self.__class__.__name__}] {message}"
        return message


class RunnableComponent(LabeledComponent):
    def __init__(self, label: Optional[str] = None):
        super().__init__(label)
        self._condition = Condition()
        self._status = COMPONENT_STATUS.STOPPED

    async def run(self):
        stop_when_exception_occurred = True
        try:
            await self._condition.acquire()
            if self._status != COMPONENT_STATUS.STOPPED:
                self._condition.release()
                message = "Cannot run when it is not stopped"
                logger.warning(self._create_message(message))
                stop_when_exception_occurred = False
                raise Exception(message)

            self._status = COMPONENT_STATUS.STARTING
            logger.debug(self._create_message("Starting"))
            self._condition.notify_all()
            self._condition.release()

            await self._run()

            logger.debug(self._create_message("Stopped"))
            await self._condition.acquire()
            self._status = COMPONENT_STATUS.STOPPED
            self._condition.notify_all()
            self._condition.release()
        except Exception as e:
            if stop_when_exception_occurred:
                self._status = COMPONENT_STATUS.STOPPED
            logger.error(self._create_message(f"Unexpected Exception: {str(e)}"))
            logger.error(traceback.format_exc())
            raise e

    async def stop(self):
        await self._condition.acquire()
        if self._status != COMPONENT_STATUS.RUNNING:
            self._condition.release()
            message = "Cannot stop when it is not running"
            logger.warning(self._create_message(message))
            raise Exception(message)

        logger.debug(self._create_message("Stopping"))
        self._status = COMPONENT_STATUS.STOPPING
        self._condition.release()

        await self._stop()

        await self._condition.acquire()
        while self._status != COMPONENT_STATUS.STOPPED:
            await self._condition.wait()
        self._condition.release()

    @abstractmethod
    async def _run(self):
        """must be implemented by a child class"""

    async def _change_status_to_running(self):
        await self._condition.acquire()
        self._status = COMPONENT_STATUS.RUNNING
        self._condition.notify_all()
        self._condition.release()

    @abstractmethod
    async def _stop(self):
        """must be implemented by a child class"""

    async def wait_for_ready(self):
        await self._condition.acquire()
        while self._status != COMPONENT_STATUS.RUNNING:
            logger.debug(self._create_message("Not running yet. Waiting"))
            await self._condition.wait()
        self._condition.release()
