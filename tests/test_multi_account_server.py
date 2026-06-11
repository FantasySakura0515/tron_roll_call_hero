"""Fake server multi-account model tests (Phase 3.2)."""

import unittest

import aiohttp

from tests.fake_tron_server import FakeTronServer


class MultiAccountServerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.fake = await FakeTronServer(
            credentials={"user1": "pass1", "user2": "pass2"}
        ).start()
        self.fake.per_account_state = True
        self.fake.rollcalls = [{"is_number": True, "rollcall_id": 42, "status": "absent"}]
        self.fake.student_rollcalls = [
            {"student_id": 1, "user_no": "user1", "status": "pending", "rollcall_status": "on_call"},
            {"student_id": 2, "user_no": "user2", "status": "pending", "rollcall_status": "on_call"},
        ]

    async def asyncTearDown(self) -> None:
        await self.fake.close()

    async def login(self, session: aiohttp.ClientSession, user: str, password: str) -> str:
        async with session.post(
            "{}/submit".format(self.fake.base_url),
            data={"username": user, "password": password},
            allow_redirects=False,
        ) as resp:
            self.assertEqual(resp.status, 302)
            cookie = resp.cookies.get("session")
            self.assertIsNotNone(cookie)
            return cookie.value

    async def get_status(self, session, path: str, cookie: str) -> int:
        async with session.get(
            "{}{}".format(self.fake.base_url, path), cookies={"session": cookie}
        ) as resp:
            return resp.status

    async def get_json(self, session, path: str, cookie: str):
        async with session.get(
            "{}{}".format(self.fake.base_url, path), cookies={"session": cookie}
        ) as resp:
            return await resp.json()

    async def test_cookies_are_per_account_and_not_interchangeable(self) -> None:
        async with aiohttp.ClientSession() as session:
            cookie_a = await self.login(session, "user1", "pass1")
            cookie_b = await self.login(session, "user2", "pass2")
            self.assertNotEqual(cookie_a, cookie_b)

            self.assertEqual(await self.get_status(session, "/api/radar/rollcalls", cookie_a), 200)
            self.assertEqual(await self.get_status(session, "/api/radar/rollcalls", cookie_b), 200)
            self.assertEqual(await self.get_status(session, "/api/radar/rollcalls", "bogus"), 401)

            # Expiring user2's session leaves user1 untouched.
            self.fake.expire_account_session("user2")
            self.assertEqual(await self.get_status(session, "/api/radar/rollcalls", cookie_b), 401)
            self.assertEqual(await self.get_status(session, "/api/radar/rollcalls", cookie_a), 200)

    async def test_request_records_identify_account(self) -> None:
        async with aiohttp.ClientSession() as session:
            cookie_b = await self.login(session, "user2", "pass2")
            async with session.put(
                "{}/api/rollcall/42/answer_number_rollcall".format(self.fake.base_url),
                json={"deviceId": "d", "numberCode": "0001"},
                cookies={"session": cookie_b},
            ) as resp:
                self.assertEqual(resp.status, 200)
        self.assertEqual(self.fake.number_attempts[-1]["user"], "user2")

    async def test_state_update_only_affects_that_account(self) -> None:
        async with aiohttp.ClientSession() as session:
            cookie_a = await self.login(session, "user1", "pass1")
            cookie_b = await self.login(session, "user2", "pass2")

            async with session.put(
                "{}/api/rollcall/42/answer_number_rollcall".format(self.fake.base_url),
                json={"deviceId": "d", "numberCode": "0001"},
                cookies={"session": cookie_a},
            ) as resp:
                self.assertEqual(resp.status, 200)

            payload = await self.get_json(session, "/api/rollcall/42/student_rollcalls", cookie_a)
            by_user = {entry["user_no"]: entry for entry in payload["student_rollcalls"]}
            self.assertEqual(by_user["user1"]["status"], "on_call_fine")
            self.assertEqual(by_user["user2"]["status"], "pending")

            feed_a = await self.get_json(session, "/api/radar/rollcalls", cookie_a)
            feed_b = await self.get_json(session, "/api/radar/rollcalls", cookie_b)
            self.assertEqual(feed_a["rollcalls"][0]["status"], "on_call_fine")
            self.assertEqual(feed_b["rollcalls"][0]["status"], "absent")

    async def test_per_account_login_failure(self) -> None:
        self.fake.fail_login_users.add("user2")
        async with aiohttp.ClientSession() as session:
            cookie_a = await self.login(session, "user1", "pass1")
            self.assertTrue(cookie_a)
            async with session.post(
                "{}/submit".format(self.fake.base_url),
                data={"username": "user2", "password": "pass2"},
                allow_redirects=False,
            ) as resp:
                self.assertEqual(resp.status, 200)
                self.assertIsNone(resp.cookies.get("session"))

    async def test_per_account_submit_failure(self) -> None:
        self.fake.fail_submit_users.add("user2")
        async with aiohttp.ClientSession() as session:
            cookie_a = await self.login(session, "user1", "pass1")
            cookie_b = await self.login(session, "user2", "pass2")
            async with session.put(
                "{}/api/rollcall/42/answer_number_rollcall".format(self.fake.base_url),
                json={"deviceId": "d", "numberCode": "0001"},
                cookies={"session": cookie_b},
            ) as resp:
                self.assertEqual(resp.status, 500)
            async with session.put(
                "{}/api/rollcall/42/answer_number_rollcall".format(self.fake.base_url),
                json={"deviceId": "d", "numberCode": "0001"},
                cookies={"session": cookie_a},
            ) as resp:
                self.assertEqual(resp.status, 200)


if __name__ == "__main__":
    unittest.main()
