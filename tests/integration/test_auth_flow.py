"""Steam OpenID auth flow against the real app (only Steam itself is mocked)."""

import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from . import harness
from .harness import IntegrationTestCase, skip_unless_integration


class FakeSteamResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


def callback_params(state, steam_id="76561198012345678", signed="claimed_id,identity,return_to"):
    return {
        "state": state,
        "openid.signed": signed,
        "openid.claimed_id": f"https://steamcommunity.com/openid/id/{steam_id}",
        "openid.identity": f"https://steamcommunity.com/openid/id/{steam_id}",
        "openid.return_to": "https://example.test/api/auth/callback",
        "openid.assoc_handle": "handle",
        "openid.sig": "sig",
        "openid.ns": "http://specs.openid.net/auth/2.0",
    }


@skip_unless_integration
class SteamAuthFlowTests(IntegrationTestCase):
    def start_login(self):
        response = self.client.get("/api/auth")
        self.assertEqual(response.status_code, 302)
        redirect = urlparse(response.headers["Location"])
        self.assertEqual(redirect.hostname, "steamcommunity.com")
        return_to = parse_qs(redirect.query)["openid.return_to"][0]
        state = parse_qs(urlparse(return_to).query)["state"][0]
        return state

    def test_check_is_unauthenticated_without_session(self):
        response = self.client.get("/api/auth/check")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertFalse(body["authenticated"])
        self.assertFalse(body["is_admin"])
        self.assertIsNone(body["csrf_token"])

    def test_full_login_flow_establishes_session_and_csrf(self):
        state = self.start_login()
        with patch(
            "app.api.auth.routes.requests.post",
            return_value=FakeSteamResponse("ns:http\nis_valid:true\n"),
        ) as steam_post:
            response = self.client.get(
                "/api/auth/callback", query_string=callback_params(state)
            )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/profile"))
        self.assertEqual(
            steam_post.call_args.kwargs["data"]["openid.mode"],
            "check_authentication",
        )

        check = self.client.get("/api/auth/check").get_json()
        self.assertTrue(check["authenticated"])
        self.assertEqual(check["steam_id"], "76561198012345678")
        self.assertTrue(check["csrf_token"])
        self.assertFalse(check["is_admin"])

    def test_callback_rejects_invalid_state(self):
        self.start_login()
        with patch("app.api.auth.routes.requests.post") as steam_post:
            response = self.client.get(
                "/api/auth/callback", query_string=callback_params("wrong-state")
            )
        self.assertEqual(response.status_code, 400)
        steam_post.assert_not_called()

    def test_callback_rejects_signature_not_covering_identity(self):
        state = self.start_login()
        with patch("app.api.auth.routes.requests.post") as steam_post:
            response = self.client.get(
                "/api/auth/callback",
                query_string=callback_params(state, signed="return_to,assoc_handle"),
            )
        self.assertEqual(response.status_code, 400)
        steam_post.assert_not_called()

    def test_callback_rejects_steam_is_valid_false(self):
        state = self.start_login()
        with patch(
            "app.api.auth.routes.requests.post",
            return_value=FakeSteamResponse("is_valid:false\n"),
        ):
            response = self.client.get(
                "/api/auth/callback", query_string=callback_params(state)
            )
        self.assertEqual(response.status_code, 401)
        self.assertFalse(self.client.get("/api/auth/check").get_json()["authenticated"])

    def test_logout_requires_csrf_and_clears_session(self):
        harness.login(self.client, "76561198012345678")

        rejected = self.client.post("/api/auth/logout")
        self.assertEqual(rejected.status_code, 403)

        headers = {"X-CSRF-Token": "integration-test-csrf-token"}
        response = self.client.post("/api/auth/logout", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.client.get("/api/auth/check").get_json()["authenticated"])

    def test_admin_flag_reflects_config(self):
        harness.admin_session(self.client)
        check = self.client.get("/api/auth/check").get_json()
        self.assertTrue(check["authenticated"])
        self.assertTrue(check["is_admin"])

