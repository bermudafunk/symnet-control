import logging
import typing

from . import common
from .common import LampState, ButtonEvent

logger = logging.getLogger(__name__)


class DummyButton(common.BaseButton):
    def add_handler(self, handler: typing.Callable[[ButtonEvent], None]):
        super().add_handler(handler)
        logger.info("Added handler {} to button {}".format(handler, self.name))

    def remove_handler(self, handler: typing.Callable[[ButtonEvent], None]):
        super().remove_handler(handler)
        logger.info("Removed handler {} to button {}".format(handler, self.name))


class DummyLamp(common.BaseLamp):
    def __init__(self, name: str):
        super().__init__(name)
        self._state = common.LampState.OFF

    @property
    def state(self) -> LampState:
        return self._state

    @state.setter
    def state(self, state: LampState):
        logger.info("Setting state of lamp {} to {}".format(self.name, state))
        if not isinstance(state, LampState):
            raise TypeError("Supplied state {} is not a instance of LampState".format(state))
        self._state = state
