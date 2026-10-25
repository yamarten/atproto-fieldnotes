import io
import unittest
import urllib.error
from unittest import mock

from helpers import SCRIPTS
from project_registry_core.transport import http_transport


class RedirectPolicyTest(unittest.TestCase):
    def test_metadata_redirect_stops_before_second_request(self):
        error = urllib.error.HTTPError("https://app.example/client.json", 302, "redirect",
                                      {"Location": "https://other.example/client.json"}, io.BytesIO())
        with mock.patch.object(http_transport, "outbound_host_is_blocked", return_value=False), \
                mock.patch.object(http_transport.NO_REDIRECT_OPENER, "open", side_effect=error) as fetch:
            result = http_transport.request_bytes_with_final_url("https://app.example/client.json", follow_redirects=False)
        self.assertEqual(result[0], 302)
        fetch.assert_called_once()

    def test_response_limit_reads_only_one_extra_byte(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b"12345"
        with mock.patch.object(http_transport, "outbound_host_is_blocked", return_value=False), \
                mock.patch.object(http_transport.NO_REDIRECT_OPENER, "open", return_value=response):
            with self.assertRaisesRegex(ValueError, "byte limit"):
                http_transport.request_bytes_with_final_url("https://app.example/client.json", max_bytes=4)
        response.read.assert_called_once_with(5)

    def request(self, target, *, status=307, headers=None, data=None):
        self.requests = []

        def open_request(request, **kwargs):
            self.requests.append(request)
            if len(self.requests) == 1:
                raise urllib.error.HTTPError(request.full_url, status, "redirect",
                                             {"Location": target}, io.BytesIO())
            response = mock.MagicMock()
            response.__enter__.return_value = response
            response.status = 200
            response.headers = {}
            response.read.return_value = b"{}"
            response.geturl.return_value = request.full_url
            return response

        with mock.patch.object(http_transport, "outbound_host_is_blocked", return_value=False), \
             mock.patch.object(http_transport.NO_REDIRECT_OPENER, "open", side_effect=open_request):
            return http_transport.request_bytes_with_final_url(
                "https://api.example/start", headers=headers, data=data,
                method="POST" if data is not None else "GET",
            )

    def test_credentials_and_api_bodies_never_reach_another_origin(self):
        for status in (301, 302, 303, 307, 308):
            for target in ("https://other.example/receive", "https://api.example:444/receive"):
                for headers, data in (({"Authorization": "Bearer synthetic"}, None),
                                      ({"cOoKiE": "session=synthetic"}, None),
                                      ({}, b'{"password":"synthetic"}')):
                    with self.subTest(status=status, target=target, headers=headers, data=data):
                        with self.assertRaisesRegex(ValueError, "cross-origin"):
                            self.request(target, status=status, headers=headers, data=data)
                        self.assertEqual(len(self.requests), 1)

    def test_same_origin_redirect_preserves_authentication_and_307_body(self):
        self.request("https://API.example:443/next", headers={"Authorization": "Bearer synthetic"}, data=b"payload")
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.requests[1].get_header("Authorization"), "Bearer synthetic")
        self.assertEqual(self.requests[1].data, b"payload")
        self.assertEqual(self.requests[1].get_method(), "POST")

    def test_same_origin_303_switches_to_get(self):
        self.request("/next", status=303, data=b"payload")
        self.assertEqual(self.requests[1].get_method(), "GET")
        self.assertIsNone(self.requests[1].data)

    def test_anonymous_page_can_redirect_to_another_public_origin(self):
        _, body, _, url = self.request("https://other.example/page")
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(body, b"{}")
        self.assertEqual(url, "https://other.example/page")

    def test_https_downgrade_is_refused_even_without_credentials(self):
        with self.assertRaisesRegex(ValueError, "downgrade"):
            self.request("http://api.example/next")
        self.assertEqual(len(self.requests), 1)
