import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from iSponsorBlockTV.ytlounge import YtLoungeApi


class LoungeSubscriptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_with_events_resumes_same_session_until_no_progress(self):
        lounge = YtLoungeApi(logger=Mock())
        lounge._sid = "existing-session"
        lounge._gsession = "existing-lounge-session"
        sessions = []

        async def subscribe():
            sessions.append((lounge._sid, lounge._gsession))
            if len(sessions) == 1:
                lounge.last_event_time += 1

        with patch("pyytlounge.YtLoungeApi.subscribe", new=AsyncMock(side_effect=subscribe)):
            task = await lounge.subscribe_monitored(Mock())
            await asyncio.wait_for(task, timeout=1)

        self.assertEqual(sessions, [("existing-session", "existing-lounge-session")] * 2)
        self.assertTrue(lounge.subscribe_task_watchdog.done())

    async def test_invalid_session_returns_to_normal_reconnection(self):
        lounge = YtLoungeApi(logger=Mock())
        lounge._sid = "existing-session"
        lounge._gsession = "existing-lounge-session"

        async def subscribe():
            lounge.last_event_time += 1
            lounge._connection_lost()

        subscription = AsyncMock(side_effect=subscribe)
        with patch("pyytlounge.YtLoungeApi.subscribe", new=subscription):
            task = await lounge.subscribe_monitored(Mock())
            await asyncio.wait_for(task, timeout=1)

        subscription.assert_awaited_once()
        self.assertFalse(lounge.connected())
        self.assertTrue(lounge.subscribe_task_watchdog.done())
