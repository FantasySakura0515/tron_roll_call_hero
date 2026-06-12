import unittest

try:
    import aiohttp
    from aiohttp import web
except (ImportError, ModuleNotFoundError):
    aiohttp = None
    web = None

from tests.fake_tron_server import FakeTronServer
from tron_roll_call_hero import tron_http


@unittest.skipUnless(aiohttp is not None and web is not None, "aiohttp.web is required")
class FakeTronServerTest(unittest.IsolatedAsyncioTestCase):
    async def test_login_and_rollcalls_use_session_cookie(self) -> None:
        async with FakeTronServer() as server:
            server.rollcalls = [{"status": "on_call_fine", "rollcall_id": 11}]
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
                form, outcome = await server.login_session(session)
                result = await server.client(session).fetch_rollcalls()

        self.assertEqual(form.fields["execution"], "abc123")
        self.assertTrue(outcome.has_session)
        self.assertEqual(result.payload["rollcalls"][0]["rollcall_id"], 11)

    async def test_course_endpoints_support_semester_courses_and_scripts(self) -> None:
        async with FakeTronServer() as server:
            server.courses = [{"id": 301, "display_name": "Compilers"}]
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
                await server.login_session(session)

                semester = await session.get(server.current_semester_url)
                courses = await session.get(server.courses_url)
                server.queue_response("courses", status=503, text="down")
                scripted = await session.get(server.courses_url)

                semester_payload = await semester.json()
                courses_payload = await courses.json()

        self.assertEqual(semester.status, 200)
        self.assertEqual(semester_payload["semester"]["id"], 1122)
        self.assertEqual(courses_payload["courses"][0]["display_name"], "Compilers")
        self.assertEqual(scripted.status, 503)

    async def test_number_endpoint_supports_success_wrong_code_and_429_script(self) -> None:
        async with FakeTronServer(correct_number_code="0427") as server:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
                await server.login_session(session)

                wrong = await session.put(
                    server.base_url + "/api/rollcall/42/answer_number_rollcall",
                    json={"numberCode": "0000", "deviceId": "dev"},
                )
                ok = await session.put(
                    server.base_url + "/api/rollcall/42/answer_number_rollcall",
                    json={"numberCode": "0427", "deviceId": "dev"},
                )
                server.queue_response("number", status=429, text="limited")
                limited = await session.put(
                    server.base_url + "/api/rollcall/42/answer_number_rollcall",
                    json={"numberCode": "1111", "deviceId": "dev"},
                )

        self.assertEqual(wrong.status, 400)
        self.assertEqual(ok.status, 200)
        self.assertEqual(limited.status, 429)
        self.assertEqual([item["body"]["numberCode"] for item in server.number_attempts], ["0000", "0427", "1111"])

    async def test_radar_and_qr_endpoints_record_payloads(self) -> None:
        async with FakeTronServer() as server:
            server.radar_lite_payload = {"use_beacon": True, "beacon_nonce": "nonce"}
            server.radar_distance = 98.7
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
                await server.login_session(session)

                lite = await session.get(server.base_url + "/api/rollcall/88/lite")
                radar = await session.put(
                    server.base_url + "/api/rollcall/88/answer",
                    json={"latitude": 24.1, "longitude": 120.1, "deviceId": "dev"},
                )
                qr = await session.put(
                    server.base_url + "/api/rollcall/88/answer_qr_rollcall",
                    json={"data": "fixture", "deviceId": "dev"},
                    headers={"x-session-id": "session-1"},
                )

                lite_payload = await lite.json()
                radar_payload = await radar.json()
                qr_payload = await qr.json()

        self.assertTrue(lite_payload["use_beacon"])
        self.assertEqual(radar.status, 400)
        self.assertEqual(radar_payload["distance"], 98.7)
        self.assertEqual(qr.status, 200)
        self.assertTrue(qr_payload["ok"])
        self.assertEqual(server.radar_answers[0]["body"]["deviceId"], "dev")
        self.assertEqual(server.radar_payload_field_names[0], ["deviceId", "latitude", "longitude"])
        self.assertEqual(server.qr_answers[0]["session_id"], "session-1")

    async def test_radar_endpoint_can_compute_dynamic_distance_and_success_radius(self) -> None:
        async with FakeTronServer() as server:
            server.set_radar_target(24.1, 120.1, success_radius_meters=8.0)
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
                await server.login_session(session)

                outside = await session.put(
                    server.base_url + "/api/rollcall/88/answer",
                    json={"latitude": 24.101, "longitude": 120.1, "deviceId": "dev"},
                )
                inside = await session.put(
                    server.base_url + "/api/rollcall/88/answer",
                    json={"latitude": 24.10001, "longitude": 120.1, "deviceId": "dev"},
                )
                outside_payload = await outside.json()
                inside_payload = await inside.json()

        self.assertEqual(outside.status, 400)
        self.assertGreater(outside_payload["distance"], 8.0)
        self.assertEqual(inside.status, 200)
        self.assertTrue(inside_payload["success"])
        self.assertEqual(len(server.radar_answers), 2)

    async def test_radar_endpoint_supports_scripted_server_error_after_recording_fields(self) -> None:
        async with FakeTronServer() as server:
            server.queue_response("radar", status=503, text="down")
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
                await server.login_session(session)
                response = await session.put(
                    server.base_url + "/api/rollcall/88/answer",
                    json={"latitude": 24.1, "longitude": 120.1, "deviceId": "dev", "accuracy": 60},
                )
                text = await response.text()

        self.assertEqual(response.status, 503)
        self.assertEqual(text, "down")
        self.assertEqual(
            server.radar_payload_field_names[0],
            ["accuracy", "deviceId", "latitude", "longitude"],
        )

    async def test_session_expired_and_5xx_scripted_rollcalls(self) -> None:
        async with FakeTronServer() as server:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
                await server.login_session(session)
                server.session_expired = True
                with self.assertRaises(tron_http.UnauthorizedError):
                    await server.client(session).fetch_rollcalls()

                server.session_expired = False
                server.queue_response("rollcalls", status=503, text="down")
                with self.assertRaises(tron_http.UnexpectedResponseError):
                    await server.client(session).fetch_rollcalls()


@unittest.skipUnless(aiohttp is not None and web is not None, "aiohttp.web is required")
class FakeCaptchaLoginTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.fake = await FakeTronServer().start()
        self.fake.captcha_login = True
        self.fake.captcha_answer = "abcd"

    async def asyncTearDown(self) -> None:
        await self.fake.close()

    async def test_login_page_has_captcha_field(self) -> None:
        async with aiohttp.ClientSession() as session:
            async with session.get(self.fake.login_url) as resp:
                html = await resp.text()
        self.assertIn('name="captcha"', html)

    async def test_captcha_image_served(self) -> None:
        async with aiohttp.ClientSession() as session:
            async with session.get(self.fake.base_url + "/captcha.jpg") as resp:
                self.assertEqual(resp.status, 200)
                body = await resp.read()
        self.assertTrue(body)

    async def test_correct_captcha_and_password_sets_session(self) -> None:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.fake.base_url + "/submit",
                data={"username": "user1", "password": "pass1", "captcha": "abcd"},
                allow_redirects=False,
            ) as resp:
                self.assertEqual(resp.status, 302)
                self.assertIsNotNone(resp.cookies.get("session"))

    async def test_wrong_captcha_no_session_and_marker(self) -> None:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.fake.base_url + "/submit",
                data={"username": "user1", "password": "pass1", "captcha": "zzzz"},
                allow_redirects=False,
            ) as resp:
                self.assertEqual(resp.status, 200)
                self.assertIsNone(resp.cookies.get("session"))
                text = await resp.text()
        self.assertIn("captcha", text.lower())

    async def test_scripted_wrong_then_correct(self) -> None:
        self.fake.captcha_wrong_remaining = 2
        async with aiohttp.ClientSession() as session:
            for _ in range(2):
                async with session.post(
                    self.fake.base_url + "/submit",
                    data={"username": "user1", "password": "pass1", "captcha": "abcd"},
                    allow_redirects=False,
                ) as resp:
                    self.assertEqual(resp.status, 200)  # forced captcha error
            async with session.post(
                self.fake.base_url + "/submit",
                data={"username": "user1", "password": "pass1", "captcha": "abcd"},
                allow_redirects=False,
            ) as resp:
                self.assertEqual(resp.status, 302)

    async def test_client_primitives_classify_builtin_form_login(self) -> None:
        from tron_roll_call_hero.tron_http import detect_captcha_field

        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
            client = self.fake.client(session)
            form = await client.fetch_login_form()
            self.assertEqual(detect_captcha_field(form), "captcha")

            image = await client.fetch_captcha_image(form.action_url)
            self.assertTrue(image)

            wrong = await client.submit_builtin_form_login(
                form, "user1", "pass1", captcha_field="captcha", captcha_answer="zzzz"
            )
            self.assertEqual(wrong.reason, "captcha_error")

            bad_pw = await client.submit_builtin_form_login(
                form, "user1", "nope", captcha_field="captcha", captcha_answer="abcd"
            )
            self.assertEqual(bad_pw.reason, "rejected")

            ok = await client.submit_builtin_form_login(
                form, "user1", "pass1", captcha_field="captcha", captcha_answer="abcd"
            )
            self.assertEqual(ok.reason, "success")
            self.assertTrue(ok.has_session)

    async def test_student_rollcalls_referer_sent_when_course_id_known(self) -> None:
        # Some TronClass tenants require a Referer on student_rollcalls or they
        # withhold number_code. Send it when the course id is known.
        import aiohttp as _aiohttp

        fake = await FakeTronServer().start()
        try:
            async with _aiohttp.ClientSession(cookie_jar=_aiohttp.CookieJar(unsafe=True)) as session:
                await fake.login_session(session)
                await fake.client(session).fetch_student_rollcalls(42, course_id="C9")
        finally:
            await fake.close()
        self.assertTrue(fake.student_rollcalls_referers)
        self.assertIn("/course/C9/rollcall", fake.student_rollcalls_referers[-1])

    async def test_wrong_password_page_with_captcha_label_is_rejected_not_captcha(self) -> None:
        # Regression: the real FJU CAS page renders the "驗證碼:" captcha label on
        # every response, including a wrong-password re-render. Classification must
        # key on the captcha-error phrase, not the ever-present label.
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
            client = self.fake.client(session)
            form = await client.fetch_login_form()
            result = await client.submit_builtin_form_login(
                form, "user1", "wrong-password", captcha_field="captcha", captcha_answer="abcd"
            )
        self.assertEqual(result.reason, "rejected")
