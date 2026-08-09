import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

from iSponsorBlockTV.main import DeviceListener
from pyytlounge.models import DpadCommand


async def immediate_timeout(awaitable, timeout):
    awaitable.close()
    raise TimeoutError


def playback_state(*, video_id="video", state=1, current_time=0.0, duration=10.0):
    return SimpleNamespace(
        videoId=video_id,
        state=SimpleNamespace(value=state),
        currentTime=current_time,
        duration=duration,
    )


def listener_for_test():
    listener = DeviceListener.__new__(DeviceListener)
    listener.task = None
    listener._end_return_task = None
    listener._playlist_transition_event = asyncio.Event()
    listener._current_video_id = "video"
    listener.api_helper = SimpleNamespace(get_segments=AsyncMock(return_value=[]))
    listener.lounge_controller = SimpleNamespace(
        auto_play=False,
        playback_speed=1.0,
        connected=MagicMock(return_value=True),
        get_now_playing=AsyncMock(),
        send_dpad_command=AsyncMock(),
    )
    listener.logger = MagicMock()
    listener.offset = 0
    return listener


class EndReturnTests(unittest.IsolatedAsyncioTestCase):
    async def test_playing_video_schedules_return_before_end(self):
        listener = listener_for_test()
        listener._return_to_playlist_before_end = AsyncMock()
        state = playback_state(current_time=7.0, duration=10.0)

        with patch("iSponsorBlockTV.main.time.monotonic", side_effect=[100.0, 100.0]):
            await listener.process_playstatus(state, time_start=100.0)
        await listener._end_return_task

        listener._return_to_playlist_before_end.assert_awaited_once()
        video_id, delay, expected_end = listener._return_to_playlist_before_end.await_args.args
        self.assertEqual(video_id, "video")
        self.assertAlmostEqual(delay, 2.0)
        self.assertAlmostEqual(expected_end, 103.0)

    async def test_video_change_cancels_scheduled_return(self):
        listener = listener_for_test()
        scheduled_return = MagicMock()
        scheduled_return.done.return_value = False
        listener._end_return_task = scheduled_return
        listener.process_playstatus = AsyncMock()

        await listener(playback_state(video_id="next-video"))
        await listener.task

        scheduled_return.cancel.assert_called_once_with()
        self.assertEqual(listener._current_video_id, "next-video")

    async def test_back_commands_are_sent_for_current_connected_video(self):
        listener = listener_for_test()

        with (
            patch("iSponsorBlockTV.main.asyncio.sleep", new=AsyncMock()),
            patch("iSponsorBlockTV.main.asyncio.wait_for", side_effect=immediate_timeout),
            patch("iSponsorBlockTV.main.time.monotonic", return_value=100.0),
        ):
            await listener._return_to_playlist_before_end("video", 0, 101.0)

        self.assertEqual(
            listener.lounge_controller.send_dpad_command.await_args_list,
            [call(DpadCommand.BACK), call(DpadCommand.BACK)],
        )

        listener.lounge_controller.send_dpad_command.reset_mock()
        listener._current_video_id = "next-video"
        with patch("iSponsorBlockTV.main.asyncio.sleep", new=AsyncMock()):
            await listener._return_to_playlist_before_end("video", 0, 101.0)

        listener.lounge_controller.send_dpad_command.assert_not_awaited()

    async def test_playlist_transition_after_first_back_stops_sequence(self):
        listener = listener_for_test()

        async def signal_transition(_command):
            listener._playlist_transition_event.set()

        listener.lounge_controller.send_dpad_command.side_effect = signal_transition

        with (
            patch("iSponsorBlockTV.main.asyncio.sleep", new=AsyncMock()),
            patch("iSponsorBlockTV.main.time.monotonic", return_value=100.0),
        ):
            await listener._return_to_playlist_before_end("video", 0, 101.0)

        listener.lounge_controller.send_dpad_command.assert_awaited_once_with(DpadCommand.BACK)

    async def test_back_sequence_stops_if_video_changes_during_transition_wait(self):
        listener = listener_for_test()

        async def change_video(awaitable, timeout):
            awaitable.close()
            listener._current_video_id = "next-video"
            raise TimeoutError

        with (
            patch("iSponsorBlockTV.main.asyncio.sleep", new=AsyncMock()),
            patch("iSponsorBlockTV.main.asyncio.wait_for", side_effect=change_video),
            patch("iSponsorBlockTV.main.time.monotonic", return_value=100.0),
        ):
            await listener._return_to_playlist_before_end("video", 0, 101.0)

        listener.lounge_controller.send_dpad_command.assert_awaited_once_with(DpadCommand.BACK)

    async def test_transition_at_timeout_suppresses_fallback_back(self):
        listener = listener_for_test()

        async def signal_then_timeout(awaitable, timeout):
            awaitable.close()
            listener._playlist_transition_event.set()
            raise TimeoutError

        with (
            patch("iSponsorBlockTV.main.asyncio.sleep", new=AsyncMock()),
            patch("iSponsorBlockTV.main.asyncio.wait_for", side_effect=signal_then_timeout),
            patch("iSponsorBlockTV.main.time.monotonic", return_value=100.0),
        ):
            await listener._return_to_playlist_before_end("video", 0, 101.0)

        listener.lounge_controller.send_dpad_command.assert_awaited_once_with(DpadCommand.BACK)

    async def test_stopped_state_without_video_signals_playlist_transition(self):
        listener = listener_for_test()
        listener.process_playstatus = AsyncMock()

        await listener(playback_state(video_id="", state=-1, duration=0.0))
        await listener.task

        self.assertTrue(listener._playlist_transition_event.is_set())

    async def test_disconnected_session_does_not_send_back_commands(self):
        listener = listener_for_test()
        listener.lounge_controller.connected.return_value = False

        with (
            patch("iSponsorBlockTV.main.asyncio.sleep", new=AsyncMock()),
            patch("iSponsorBlockTV.main.time.monotonic", return_value=100.0),
        ):
            await listener._return_to_playlist_before_end("video", 0, 101.0)

        listener.lounge_controller.send_dpad_command.assert_not_awaited()

    async def test_long_video_refreshes_position_before_returning(self):
        listener = listener_for_test()
        sleep = AsyncMock()

        with (
            patch("iSponsorBlockTV.main.asyncio.sleep", new=sleep),
            patch("iSponsorBlockTV.main.asyncio.wait_for", side_effect=immediate_timeout),
            patch("iSponsorBlockTV.main.time.monotonic", return_value=140.0),
        ):
            await listener._return_to_playlist_before_end("video", 100.0, 200.0)

        listener.lounge_controller.get_now_playing.assert_awaited_once_with()
        self.assertEqual(
            sleep.await_args_list,
            [call(40.0), call(59.0)],
        )
        self.assertEqual(
            listener.lounge_controller.send_dpad_command.await_args_list,
            [call(DpadCommand.BACK), call(DpadCommand.BACK)],
        )

    async def test_segment_ending_with_video_is_not_skipped(self):
        listener = listener_for_test()
        listener.skip = AsyncMock()
        segment = {"start": 5.0, "end": 99.0, "UUID": "segment-id"}

        await listener.time_to_segment(
            [segment],
            position=0.0,
            video_duration=100.0,
            time_start=100.0,
        )

        listener.skip.assert_not_awaited()

    async def test_regular_segment_is_still_skipped(self):
        listener = listener_for_test()
        listener.skip = AsyncMock()
        segment = {"start": 5.0, "end": 20.0, "UUID": "segment-id"}

        with patch("iSponsorBlockTV.main.time.monotonic", return_value=100.0):
            await listener.time_to_segment(
                [segment],
                position=0.0,
                video_duration=100.0,
                time_start=100.0,
            )

        listener.skip.assert_awaited_once_with(5.0, 20.0, "segment-id")


if __name__ == "__main__":
    unittest.main()
