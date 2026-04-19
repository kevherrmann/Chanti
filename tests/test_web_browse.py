"""Tests für web_browse.py — SSRF-Schutz."""
import pytest

from conftest import load_skill

wb = load_skill("web_browse")


@pytest.mark.parametrize("url", [
    "https://example.com",
    "http://example.com/foo",
    "https://example.com:8080/path",
])
def test_public_urls_accepted(url):
    ok, _ = wb._validate_url(url)
    assert ok is True


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ssh://example.com",
    "javascript:alert(1)",
    "ftp://example.com",
    "data:text/html,<script>",
])
def test_non_http_schemes_rejected(url):
    ok, msg = wb._validate_url(url)
    assert ok is False


@pytest.mark.parametrize("url", [
    "http://localhost/",
    "http://127.0.0.1/",
    "http://192.168.1.1/",
    "http://10.0.0.1/",
    "http://172.16.0.1/",
    "http://169.254.169.254/",   # AWS/Cloud metadata
    "http://[::1]/",
    "http://[fe80::1]/",
])
def test_private_hosts_rejected(url):
    ok, msg = wb._validate_url(url)
    assert ok is False


def test_missing_host_rejected():
    ok, _ = wb._validate_url("http://")
    assert ok is False


def test_ipv6_public_accepted():
    # 2001:db8::1 ist Documentation-Range, nicht privat
    # (aber getaddrinfo versucht trotzdem DNS — wir prüfen nur
    # dass direkte IP-Prüfung funktioniert)
    ok, _ = wb._validate_url("http://[2606:4700:4700::1111]/")  # Cloudflare DNS
    assert ok is True


def test_is_private_ip_detects_all_private_ranges():
    assert wb._is_private_ip("10.0.0.1") is True
    assert wb._is_private_ip("192.168.1.1") is True
    assert wb._is_private_ip("172.16.5.5") is True
    assert wb._is_private_ip("127.0.0.1") is True
    assert wb._is_private_ip("169.254.1.1") is True
    assert wb._is_private_ip("::1") is True
    assert wb._is_private_ip("fe80::1") is True
    assert wb._is_private_ip("8.8.8.8") is False
    assert wb._is_private_ip("not-an-ip") is False
