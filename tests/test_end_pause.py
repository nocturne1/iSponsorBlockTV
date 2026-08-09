import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

from iSponsorBlockTV.main import DeviceListener
from pyytlounge.models import DpadCommand


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
    listener._end_pause_task = None
    listener._current_video_id = "video"
    listener.api_helper = SimpleNamespace(get_segments=AsyncMock(return_value=[]))
    listener.lounge_controller = SimpleNamespace(
        auto_play=False,
        playback_speed=1.0,
        connected=MagicMock(return_value=True),
        get_now_playing=AsyncMock(),
        pause=AsyncMock(),
        send_dpad_command=AsyncMock(),
    )
    listener.logger = MagicMock()
    listener.offset = 0
    return listener


class EndPauseTests(unittest.IsolatedAsyncioTestCase):
    async def test_playing_video_schedules_pause_before_end(self):
        listener = listener_for_test()
        listener._pause_current_video_before_end = AsyncMock()
        state = playback_state(current_time=7.0, duration=10.0)

        with patch("iSponsorBlockTV.main.time.monotonic", side_effect=[100.0, 100.0]):
            await listener.process_playstatus(state, time_start=100.0)
        await listener._end_pause_task

        listener._pause_current_video_before_end.assert_awaited_once()
        video_id, delay, expected_end = listener._pause_current_video_before_end.await_args.args
        self.assertEqual(video_id, "video")
        self.assertAlmostEqual(delay, 1.5)
        self.assertAlmostEqual(expected_end, 103.0)

    async def test_video_change_cancels_scheduled_pause(self):
        listener = listener_for_test()
        scheduled_pause = MagicMock()
        scheduled_pause.done.return_value = False
        listener._end_pause_task = scheduled_pause
        listener.process_playstatus = AsyncMock()

        await listener(playback_state(video_id="next-video"))
        await listener.task

        scheduled_pause.cancel.assert_called_once_with()
        self.assertEqual(listener._current_video_id, "next-video")

    async def test_pause_is_sent_only_for_current_connected_video(self):
        listener = listener_for_test()

        with (
            patch("iSponsorBlockTV.main.asyncio.sleep", new=AsyncMock()),
            patch("iSponsorBlockTV.main.time.monotonic", return_value=100.0),
        ):
            await listener._pause_current_video_before_end("video", 0, 101.0)

        listener.lounge_controller.pause.assert_awaited_once_with()
        self.assertEqual(
            listener.lounge_controller.send_dpad_command.await_args_list,
            [call(DpadCommand.BACK), call(DpadCommand.BACK)],
        )

        listener.lounge_controller.pause.reset_mock()
        listener.lounge_controller.send_dpad_command.reset_mock()
        listener._current_video_id = "next-video"
        with patch("iSponsorBlockTV.main.asyncio.sleep", new=AsyncMock()):
            await listener._pause_current_video_before_end("video", 0, 101.0)

        listener.lounge_controller.pause.assert_not_awaited()
        listener.lounge_controller.send_dpad_command.assert_not_awaited()

    async def test_back_sequence_stops_if_video_changes_after_pause(self):
        listener = listener_for_test()
        sleep_count = 0

        async def change_video(_delay):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count == 2:
                listener._current_video_id = "next-video"

        with (
            patch("iSponsorBlockTV.main.asyncio.sleep", side_effect=change_video),
            patch("iSponsorBlockTV.main.time.monotonic", return_value=100.0),
        ):
            await listener._pause_current_video_before_end("video", 0, 101.0)

        listener.lounge_controller.pause.assert_awaited_once_with()
        listener.lounge_controller.send_dpad_command.assert_not_awaited()

    async def test_pause_failure_does_not_send_back_commands(self):
        listener = listener_for_test()
        listener.lounge_controller.pause.side_effect = RuntimeError("pause failed")

        with (
            patch("iSponsorBlockTV.main.asyncio.sleep", new=AsyncMock()),
            patch("iSponsorBlockTV.main.time.monotonic", return_value=100.0),
        ):
            await listener._pause_current_video_before_end("video", 0, 101.0)

        listener.lounge_controller.send_dpad_command.assert_not_awaited()

    async def test_long_video_refreshes_position_before_pausing(self):
        listener = listener_for_test()
        sleep = AsyncMock()

        with (
            patch("iSponsorBlockTV.main.asyncio.sleep", new=sleep),
            patch("iSponsorBlockTV.main.time.monotonic", return_value=140.0),
        ):
            await listener._pause_current_video_before_end("video", 100.0, 200.0)

        listener.lounge_controller.get_now_playing.assert_awaited_once_with()
        self.assertEqual(
            sleep.await_args_list,
            [call(40.0), call(58.5), call(1.0), call(1.0)],
        )
        listener.lounge_controller.pause.assert_awaited_once_with()

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
