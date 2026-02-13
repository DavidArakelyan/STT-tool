"""Tests for SSRF URL validation."""

import pytest

from stt_service.utils.url_validation import validate_external_url


class TestValidateExternalUrl:

    def test_https_public_url(self):
        url = "https://example.com/webhook"
        assert validate_external_url(url) == url

    def test_http_public_url(self):
        url = "http://example.com/callback"
        assert validate_external_url(url) == url

    def test_rejects_ftp_scheme(self):
        with pytest.raises(ValueError, match="scheme must be http or https"):
            validate_external_url("ftp://example.com/file")

    def test_rejects_file_scheme(self):
        with pytest.raises(ValueError, match="scheme must be http or https"):
            validate_external_url("file:///etc/passwd")

    def test_rejects_no_scheme(self):
        with pytest.raises(ValueError, match="scheme must be http or https"):
            validate_external_url("example.com/webhook")

    def test_rejects_localhost(self):
        with pytest.raises(ValueError, match="blocked address"):
            validate_external_url("http://localhost/admin")

    def test_rejects_127_0_0_1(self):
        with pytest.raises(ValueError, match="blocked address"):
            validate_external_url("http://127.0.0.1:6379")

    def test_rejects_private_10_network(self):
        with pytest.raises(ValueError, match="blocked address"):
            validate_external_url("http://10.0.0.1:8080")

    def test_rejects_private_172_network(self):
        with pytest.raises(ValueError, match="blocked address"):
            validate_external_url("http://172.16.0.1:5432")

    def test_rejects_private_192_168(self):
        with pytest.raises(ValueError, match="blocked address"):
            validate_external_url("http://192.168.1.1")

    def test_rejects_link_local_metadata(self):
        """Block cloud metadata endpoint (AWS/GCP)."""
        with pytest.raises(ValueError, match="blocked address"):
            validate_external_url("http://169.254.169.254/latest/meta-data/")

    def test_rejects_zero_network(self):
        with pytest.raises(ValueError, match="blocked address"):
            validate_external_url("http://0.0.0.0:8000")

    def test_rejects_missing_hostname(self):
        with pytest.raises(ValueError, match="hostname"):
            validate_external_url("http://")

    def test_rejects_unresolvable_hostname(self):
        with pytest.raises(ValueError, match="Cannot resolve"):
            validate_external_url("http://this-host-does-not-exist-xyz123.invalid/path")

    def test_url_with_path_and_query(self):
        url = "https://example.com/api/webhook?token=abc"
        assert validate_external_url(url) == url
