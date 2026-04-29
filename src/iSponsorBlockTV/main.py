import asyncio
import logging
import time
from signal import SIGINT, SIGTERM, signal
from typing import Optional

import aiohttp

from . import api_helpers, ytlounge
from .debug_helpers import AiohttpTracer


class DeviceListener:
    def __init__(self, api_helper, config, device, debug: bool, web_session):
        self.task: Optional[asyncio.Task] = None
        self._end_pause_task: Optional[asyncio.Task] = None
        self.api_helper = api_helper
        self.offset = device.offset
        self.name = device.name
        self.cancelled = False
        self._current_video_id = None
        self.logger = logging.getLogger(f"iSponsorBlockTV-{device.screen_id}")
        self.web_session = web_session
        self.lounge_controller = ytlounge.YtLoungeApi(
            device.screen_id, config, api_helper, self.logger
        )

    # Ensures that we have a valid auth token
    async def refresh_auth_loop(self):
        while True:
            await asyncio.sleep(60 * 60 * 24)  # Refresh every 24 hours
            try:
                await self.lounge_controller.refresh_auth()
            except BaseException:
                pass

    async def is_available(self):
        try:
            if await self.lounge_controller.is_available():
                return True
            # get_screen_availability can return empty even when the screen is
            # reachable (e.g. after a lounge session reset). Fall back to a
            # direct connect probe: if connect() sets _sid/_gsession the screen
            # is actually up.
            self.logger.debug(
                "is_available() returned False, probing via connect()"
            )
            try:
                connected = await self.lounge_controller.connect()
                if connected:
                    self.logger.info(
                        "Screen reachable via connect() despite is_available()=False"
                    )
                return connected
            except BaseException:
                return False
        except BaseException:
            return False

    # Main subscription loop
    async def loop(self):
        lounge_controller = self.lounge_controller
        while not self.cancelled:
            while not lounge_controller.linked():
                try:
                    self.logger.debug("Refreshing auth")
                    await lounge_controller.refresh_auth()
                except BaseException:
                    await asyncio.sleep(10)
            while not (await self.is_available()) and not self.cancelled:
                self.logger.debug("Waiting for device to be available")
                await asyncio.sleep(10)
            try:
                await lounge_controller.connect()
            except BaseException:
                pass
            while not lounge_controller.connected() and not self.cancelled:
                # Doesn't connect to the device if it's a kids profile (it's broken)
                self.logger.debug("Waiting for device to be connected")
                await asyncio.sleep(10)
                try:
                    await lounge_controller.connect()
                except BaseException:
                    pass
            self.logger.info(
                "Connected to device %s (%s)", lounge_controller.screen_name, self.name
            )
            try:
                self.logger.debug("Subscribing to lounge")
                sub = await lounge_controller.subscribe_monitored(self)
                await sub
            except BaseException:
                pass
            if lounge_controller.shorts_disconnected:
                self.logger.info(
                    "Disconnected by Shorts player, waiting 5 minutes before reconnecting"
                )
                lounge_controller.shorts_disconnected = False
                await asyncio.sleep(300)

    # Method called on playback state change
    async def __call__(self, state):
        time_start = time.monotonic()
        try:
            self.task.cancel()
        except BaseException:
            pass
        if state.videoId and state.videoId != self._current_video_id:
            if self._end_pause_task and not self._end_pause_task.done():
                self.logger.debug(
                    "Cancelling pre-end pause for %s because playback moved to %s",
                    self._current_video_id,
                    state.videoId,
                )
                self._end_pause_task.cancel()
            self._current_video_id = state.videoId
        self.task = asyncio.create_task(self.process_playstatus(state, time_start))

    # Processes the playback state change
    async def process_playstatus(self, state, time_start):
        segments = []
        if state.videoId:
            segments = await self.api_helper.get_segments(state.videoId)
        if state.state.value == 1:  # Playing
            self.logger.info("Playing video %s with %d segments", state.videoId, len(segments))
            if not self.lounge_controller.auto_play and state.duration > 0:
                elapsed = time.monotonic() - time_start
                time_remaining = (
                    (state.duration - state.currentTime)
                    / self.lounge_controller.playback_speed
                )
                pause_before_end = 1.5
                time_to_pause = time_remaining - elapsed - pause_before_end
                expected_end = time.monotonic() + max(time_remaining - elapsed, 0)
                if time_to_pause > 0:
                    if self._end_pause_task and not self._end_pause_task.done():
                        self.logger.debug(
                            "Rescheduling pre-end pause for %s",
                            state.videoId,
                        )
                        self._end_pause_task.cancel()
                    self.logger.info(
                        "Scheduling current-video pre-end pause for %s in %.1fs "
                        "(%.1fs before %.1fs)",
                        state.videoId,
                        time_to_pause,
                        pause_before_end,
                        state.duration,
                    )
                    self._end_pause_task = asyncio.create_task(
                        self._pause_current_video_before_end(
                            state.videoId,
                            time_to_pause,
                            expected_end,
                        )
                    )
                else:
                    self.logger.info(
                        "Not scheduling pre-end pause for %s; only %.1fs remaining",
                        state.videoId,
                        time_remaining,
                    )
            if segments:  # If there are segments
                await self.time_to_segment(segments, state.currentTime, state.duration, time_start)

    async def _pause_current_video_before_end(self, video_id, delay, expected_end):
        if delay > 90:
            # Sleep most of the delay, then request a position refresh ~60s before
            # expected end to correct for long-sleep drift.  The nowPlaying response
            # will cancel and reschedule this task with corrected timing.
            await asyncio.sleep(max(delay - 60, 0))
            if video_id != self._current_video_id:
                self.logger.info(
                    "Skipping pre-end pause for %s; current video is %s",
                    video_id,
                    self._current_video_id,
                )
                return
            self.logger.debug("Requesting position refresh for %s before end", video_id)
            try:
                await self.lounge_controller.get_now_playing()
            except Exception:
                pass
            remaining = expected_end - time.monotonic() - 1.5
            if remaining > 0:
                await asyncio.sleep(remaining)
        else:
            await asyncio.sleep(delay)
        if video_id != self._current_video_id:
            self.logger.info(
                "Skipping pre-end pause for %s; current video is %s",
                video_id,
                self._current_video_id,
            )
            return
        if time.monotonic() >= expected_end:
            self.logger.warning(
                "Skipping pre-end pause for %s because wake-up was at or past expected end",
                video_id,
            )
            return
        if not self.lounge_controller.has_lounge_screen():
            self.logger.warning(
                "Skipping pre-end pause for %s because no LOUNGE_SCREEN receiver is attached",
                video_id,
            )
            return
        try:
            self.logger.info("Pausing current video %s before end", video_id)
            await self.lounge_controller.pause()
        except Exception:
            self.logger.exception("Failed to pause current video %s before end", video_id)

    # Finds the next segment to skip to and skips to it
    async def time_to_segment(self, segments, position, video_duration, time_start):
        start_next_segment = None
        next_segment = None
        for segment in segments:
            segment_start = segment["start"]
            segment_end = segment["end"]
            is_within_start_range = (
                position < 1 < segment_end and segment_start <= position < segment_end
            )
            is_beyond_current_position = segment_start > position

            if is_within_start_range or is_beyond_current_position:
                next_segment = segment
                start_next_segment = position if is_within_start_range else segment_start
                break
        if start_next_segment:
            # If auto_play is disabled and a segment ends within 2s of the video end,
            # let YouTube finish naturally so Watch Later can return to the list.
            if (not self.lounge_controller.auto_play and video_duration > 0
                    and next_segment["end"] >= video_duration - 2.0):
                self.logger.info(
                    "Skipping segment seek to %.3fs (within 2s of video end %.3fs) — "
                    "native end-of-video handling will handle it",
                    next_segment["end"], video_duration,
                )
                return
            time_to_next = (
                (start_next_segment - position - (time.monotonic() - time_start))
                / self.lounge_controller.playback_speed
            ) - self.offset
            await self.skip(time_to_next, next_segment["end"], next_segment["UUID"])

    # Skips to the next segment (waits for the time to pass)
    async def skip(self, time_to, position, uuids):
        await asyncio.sleep(time_to)
        self.logger.info("Skipping segment: seeking to %s", position)
        await asyncio.gather(
            asyncio.create_task(self.lounge_controller.seek_to(position)),
            asyncio.create_task(self.api_helper.mark_viewed_segments(uuids)),
        )

    async def cancel(self):
        self.cancelled = True
        await self.lounge_controller.disconnect()
        if self.task:
            self.task.cancel()
        if self._end_pause_task:
            self._end_pause_task.cancel()
        if self.lounge_controller.subscribe_task_watchdog:
            self.lounge_controller.subscribe_task_watchdog.cancel()
        if self.lounge_controller.subscribe_task:
            self.lounge_controller.subscribe_task.cancel()
        await asyncio.gather(
            self.task,
            self._end_pause_task,
            self.lounge_controller.subscribe_task_watchdog,
            self.lounge_controller.subscribe_task,
            return_exceptions=True,
        )

    async def initialize_web_session(self):
        await self.lounge_controller.change_web_session(self.web_session)


async def finish(devices, web_session, tcp_connector):
    await asyncio.gather(*(device.cancel() for device in devices), return_exceptions=True)
    await web_session.close()
    await tcp_connector.close()


def handle_signal(signum, frame):
    raise KeyboardInterrupt()


async def main_async(config, debug, http_tracing):
    loop = asyncio.get_event_loop_policy().get_event_loop()
    tasks = []  # Save the tasks so the interpreter doesn't garbage collect them
    devices = []  # Save the devices to close them later
    if debug:
        loop.set_debug(True)

    tcp_connector = aiohttp.TCPConnector(ttl_dns_cache=300)

    # Configure session with tracing if enabled
    if http_tracing:
        root_logger = logging.getLogger("aiohttp_trace")
        tracer = AiohttpTracer(root_logger)
        trace_config = aiohttp.TraceConfig()
        trace_config.on_request_start.append(tracer.on_request_start)
        trace_config.on_response_chunk_received.append(tracer.on_response_chunk_received)
        trace_config.on_request_end.append(tracer.on_request_end)
        trace_config.on_request_exception.append(tracer.on_request_exception)
        web_session = aiohttp.ClientSession(
            trust_env=config.use_proxy, connector=tcp_connector, trace_configs=[trace_config]
        )
    else:
        web_session = aiohttp.ClientSession(trust_env=config.use_proxy, connector=tcp_connector)

    api_helper = api_helpers.ApiHelper(config, web_session)
    for i in config.devices:
        device = DeviceListener(api_helper, config, i, debug, web_session)
        devices.append(device)
        await device.initialize_web_session()
        tasks.append(loop.create_task(device.loop()))
        tasks.append(loop.create_task(device.refresh_auth_loop()))
    signal(SIGTERM, handle_signal)
    signal(SIGINT, handle_signal)
    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        print("Cancelling tasks and exiting...")
        await finish(devices, web_session, tcp_connector)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        await web_session.close()
        await tcp_connector.close()
        print("Exited")


def main(config, debug, http_tracing):
    asyncio.run(main_async(config, debug, http_tracing))
