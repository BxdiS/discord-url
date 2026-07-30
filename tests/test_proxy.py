import pytest

from dcurl.proxy import ProxyRotator


def test_empty_rotator():
    r = ProxyRotator()
    assert not r.enabled
    assert r.next() is None


def test_single_proxy():
    r = ProxyRotator(["http://proxy:8080"])
    assert r.enabled
    assert r.next() == "http://proxy:8080"
    assert r.next() == "http://proxy:8080"


def test_rotates_through_list():
    urls = ["proxy1", "proxy2", "proxy3"]
    r = ProxyRotator(urls)
    for _ in range(10):
        assert r.next() == "proxy1"
        assert r.next() == "proxy2"
        assert r.next() == "proxy3"
