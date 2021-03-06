import asyncio
import logging
import re
import typing

from bermudafunk import base

logger = logging.getLogger(__name__)

SymNetRawControllerState = typing.NamedTuple('SymNetRawControllerState', [('controller_number', int), ('controller_value', int)])


class SymNetRawProtocolCallback:
    def __init__(self, callback: typing.Callable, expected_lines: int, regex: str = None):
        self._callback = callback
        self.expected_lines = expected_lines
        self.regex = regex
        self.future = base.loop.create_future()

    def callback(self, *args, **kwargs):
        logger.debug("raw protocol callback called")
        try:
            result = self._callback(*args, **kwargs)
            self.future.set_result(result)
        except Exception as e:
            self.future.set_exception(e)


class SymNetRawProtocol(asyncio.DatagramProtocol):
    def __init__(self, state_queue: asyncio.Queue):
        logger.debug("init a SymNetRawProtocol")
        self.transport = None
        self.callback_queue = []  # type: typing.List[SymNetRawProtocolCallback]
        self.state_queue = state_queue

    def connection_made(self, transport: asyncio.BaseTransport):
        logger.debug("connection established")
        self.transport = transport

    def datagram_received(self, data: bytes, address):
        logger.debug("a datagram was received - %d bytes", len(data))
        data_str = data.decode()
        lines = data_str.split('\r')
        lines = [lines[i] for i in range(len(lines)) if len(lines[i]) > 0]

        logger.debug("%d non-empty lines received", len(lines))

        if len(self.callback_queue) > 0:
            logger.debug("iterate over callback queue")
            for callback_obj in self.callback_queue:
                if len(lines) == 1 and lines[0] == 'NAK':
                    logger.debug("got only a NAK - forwarding to the first callback")
                    callback_obj.callback(data_str)
                    self.callback_queue.remove(callback_obj)
                    return

                if callback_obj.regex is not None:
                    logger.debug("callback comes with a regex - try match on the whole received data string")
                    m = re.match(callback_obj.regex, data_str)
                    if m is not None:
                        logger.debug("regex worked - deliver to callback and remove it")
                        callback_obj.callback(data_str, m=m)
                        self.callback_queue.remove(callback_obj)
                        return
                elif len(lines) == callback_obj.expected_lines:
                    logger.debug("callback has no regex, but the expected line count equals to the received one")
                    callback_obj.callback(data_str)
                    self.callback_queue.remove(callback_obj)
                    return

        if len(lines) == 1:
            if lines[0] == 'NAK':
                logger.error('Uncaught NAK - this is probably a huge error')
                return
            if lines[0] == 'ACK':
                logger.debug('got an ACK, but no callbacks waiting for input - just ignore it')
                return

        logger.debug("no callbacks defined and not an ACK or NAK - must be pushed data")
        for line in lines:
            m = re.match('^#([0-9]{5})=(-?[0-9]{4,5})$', line)
            if m is None:
                logger.error("error in in the received line <%s>", line)
                continue

            asyncio.ensure_future(self.state_queue.put(SymNetRawControllerState(
                controller_number=int(m.group(1)),
                controller_value=int(m.group(2))
            )))

    def error_received(self, exc):
        logger.error('Error received %s', exc)
        pass

    def write(self, data: str):
        logger.debug('send data to symnet %s', data)
        self.transport.sendto(data.encode())


class SymNetController:
    value_timeout = 10  # in seconds

    def __init__(self, controller_number: int, protocol: SymNetRawProtocol):
        logger.debug('create new SymNetController with %d', controller_number)
        self.controller_number = int(controller_number)
        self.proto = protocol

        self.raw_value = 0
        self.raw_value_time = 0

        self.observer = []  # type: typing.List[typing.Callable]

        base.loop.run_until_complete(self._retrieve_current_state().future)

    def add_observer(self, callback: typing.Callable):
        logger.debug("add a observer (%s) to controller %d", callback, self.controller_number)
        return self.observer.append(callback)

    def remove_observer(self, callback: typing.Callable):
        logger.debug("remove a observer (%s) to controller %d", callback, self.controller_number)
        return self.observer.remove(callback)

    async def _get_raw_value(self) -> int:
        logger.debug('retrieve current value for controller %d', self.controller_number)
        if base.loop.time() - self.raw_value_time > self.value_timeout:
            logger.debug('value timeout - refresh')
            await self._retrieve_current_state().future
        return self.raw_value

    def _set_raw_value(self, value: int):
        logger.debug('set_raw_value called on %d with %d', self.controller_number, value)
        old_value = self.raw_value
        self.raw_value = value
        self.raw_value_time = base.loop.time()
        if old_value != value:
            logger.debug("value has changed - notify observers")
            for clb in self.observer:
                base.loop.create_task(clb(self, old_value=old_value, new_value=value))

    def _assure_current_state(self):
        logger.debug("assure current controller %d state to set on the symnet device", self.controller_number)
        callback_obj = SymNetRawProtocolCallback(
            callback=self._assure_callback,
            expected_lines=1,
            regex='^(ACK)|(NAK)\r$'
        )
        self.proto.callback_queue.append(callback_obj)
        self.proto.write('CS {cn:d} {cv:d}\r'.format(cn=self.controller_number, cv=self.raw_value))
        return callback_obj

    def _assure_callback(self, _, m=None):
        if m is None or m.group(1) == 'NAK':
            raise Exception(
                'Unknown error occurred awaiting the acknowledge of setting controller number {:d}'.format(
                    self.controller_number))

    def _retrieve_current_state(self):
        logger.debug("request current value from the symnet device for controller %d", self.controller_number)
        callback_obj = SymNetRawProtocolCallback(
            callback=self._retrieve_callback,
            expected_lines=1,
            regex='^' + str(self.controller_number) + ' ([0-9]{1,5})\r$'
        )
        self.proto.callback_queue.append(callback_obj)
        self.proto.write('GS2 {:d}\r'.format(self.controller_number))
        return callback_obj

    def _retrieve_callback(self, _, m=None):
        if m is None:
            raise Exception('Error executing GS2 command, controller {}'.format(self.controller_number))
        self._set_raw_value(int(m.group(1)))


class SymNetSelectorController(SymNetController):
    def __init__(self, controller_number: int, position_cont: int, protocol: SymNetRawProtocol):
        super().__init__(controller_number, protocol)

        self._position_count = int(position_cont)

    @property
    def position_count(self) -> int:
        return self._position_count

    async def get_position(self):
        return int(round(await self._get_raw_value() / 65535 * (self.position_count - 1) + 1))

    async def set_position(self, position: int):
        assert 1 <= position <= self.position_count
        self._set_raw_value(int(round((position - 1) / (self.position_count - 1) * 65535)))
        await self._assure_current_state().future


class SymNetSelectorControllerDummy(SymNetSelectorController):
    def __init__(self, controller_number: int, position_cont: int):
        logger.debug('create new SymNetSelectorControllerDummy with %d', controller_number)
        self._position_count = int(position_cont)
        self.controller_number = int(controller_number)

        self.raw_value = 0
        self.raw_value_time = 0

        self.observer = []  # type: typing.List[typing.Callable]

    def add_observer(self, callback: typing.Callable):
        logger.debug("add a observer (%s) to controller %d", callback, self.controller_number)
        return self.observer.append(callback)

    def remove_observer(self, callback: typing.Callable):
        logger.debug("remove a observer (%s) to controller %d", callback, self.controller_number)
        return self.observer.remove(callback)

    async def _get_raw_value(self) -> int:
        logger.debug('retrieve current value for controller %d', self.controller_number)
        return self.raw_value

    def _set_raw_value(self, value: int):
        logger.debug('set_raw_value called on %d with %d', self.controller_number, value)
        old_value = self.raw_value
        self.raw_value = value
        self.raw_value_time = base.loop.time()
        if old_value != value:
            logger.debug("value has changed - notify observers")
            for clb in self.observer:
                base.loop.create_task(clb(self, old_value=old_value, new_value=value))

    def _assure_current_state(self):
        raise NotImplementedError("Dummy implementation")

    def _assure_callback(self, _, m=None):
        raise NotImplementedError("Dummy implementation")

    def _retrieve_current_state(self):
        logger.debug("request current value from the symnet device for controller %d", self.controller_number)
        callback_obj = SymNetRawProtocolCallback(
            callback=self._retrieve_callback,
            expected_lines=1,
            regex='^' + str(self.controller_number) + ' ([0-9]{1,5})\r$'
        )
        self.proto.callback_queue.append(callback_obj)
        self.proto.write('GS2 {:d}\r'.format(self.controller_number))
        return callback_obj

    def _retrieve_callback(self, _, m=None):
        if m is None:
            raise Exception('Error executing GS2 command, controller {}'.format(self.controller_number))
        self._set_raw_value(int(m.group(1)))

    async def get_position(self):
        return int(round(await self._get_raw_value() / 65535 * (self.position_count - 1) + 1))

    async def set_position(self, position: int):
        assert 1 <= position <= self.position_count
        self._set_raw_value(int(round((position - 1) / (self.position_count - 1) * 65535)))


class SymNetButtonController(SymNetController):
    async def on(self):
        self._set_raw_value(65535)
        await self._assure_current_state().future

    async def off(self):
        self._set_raw_value(0)
        await self._assure_current_state().future

    async def pressed(self):
        return await self._get_raw_value() > 0

    def set(self, state: bool):
        if state:
            return self.on()
        else:
            return self.off()


class SymNetDevice:
    controllers = ...  # type: typing.Dict[int, SymNetController]

    def __init__(self, local_address: typing.Tuple[str, int], remote_address: typing.Tuple[str, int]):
        self._state_queue = asyncio.Queue(loop=base.loop)

        def create_protocol() -> asyncio.DatagramProtocol:
            return SymNetRawProtocol(state_queue=self._state_queue)

        logger.debug('setup new symnet device')
        self.controllers = {}
        connect = base.loop.create_datagram_endpoint(
            create_protocol,
            local_addr=local_address,
            remote_addr=remote_address
        )
        self.transport, self.protocol = base.loop.run_until_complete(connect)

        self._process_task = base.loop.create_task(self._process_push_messages())
        base.cleanup_tasks.append(base.loop.create_task(self._cleanup()))

    async def _process_push_messages(self):
        while True:
            cs = await self._state_queue.get()  # type: SymNetRawControllerState
            logger.debug("received some pushed data - handover to the controller object")
            if cs.controller_number in self.controllers:
                # noinspection PyProtectedMember
                self.controllers[cs.controller_number]._set_raw_value(cs.controller_value)

    def define_controller(self, controller_number: int) -> SymNetController:
        logger.debug('create new controller %d on symnet device', controller_number)
        controller_number = int(controller_number)
        controller = SymNetController(controller_number, self.protocol)
        self.controllers[controller_number] = controller

        return controller

    def define_selector(self, controller_number: int, position_count: int) -> SymNetSelectorController:
        logger.debug('create new selector %d on symnet device', controller_number)
        controller_number = int(controller_number)
        controller = SymNetSelectorController(controller_number, position_count, self.protocol)
        self.controllers[controller_number] = controller

        return controller

    def define_button(self, controller_number: int) -> SymNetButtonController:
        logger.debug('create new button %d on symnet device', controller_number)
        controller_number = int(controller_number)
        controller = SymNetButtonController(controller_number, self.protocol)
        self.controllers[controller_number] = controller

        return controller

    async def _cleanup(self):
        logger.debug('SymNetDevice awaiting cleanup')
        await base.cleanup_event.wait()
        logger.debug('SymNetDevice cancel process_task')
        self._process_task.cancel()
        logger.debug('SymNetDevice close transport')
        self.transport.close()
