import pytest

from dcurl.proxy_parse import expand_port_range, parse_proxy, parse_proxy_file


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("http://proxy.example.com:8080", "http://proxy.example.com:8080"),
        ("https://proxy.example.com:443", "https://proxy.example.com:443"),
        ("socks5://proxy.example.com:1080", "socks5://proxy.example.com:1080"),
        ("pool.proxys.world:10000:user123:pass456", "http://user123:pass456@pool.proxys.world:10000"),
        ("example.com:8080", "http://example.com:8080"),
        ("  pool.proxys.world:10001:user:pass  ", "http://user:pass@pool.proxys.world:10001"),
        ("# comment", None),
        ("", None),
        ("invalid", None),
        ("only:one", "http://only:one"),
    ],
)
def test_parse_proxy_formats(raw, expected):
    assert parse_proxy(raw) == expected


def test_parse_proxy_file_with_comments_and_blanks():
    content = """
# Комментарий
pool.proxys.world:10000:user:pass
pool.proxys.world:10001:user:pass

http://direct.example.com:8080
"""
    urls = parse_proxy_file(content)
    assert len(urls) == 3
    assert "http://user:pass@pool.proxys.world:10000" in urls
    assert "http://direct.example.com:8080" in urls


def test_parse_proxy_file_dedupes():
    content = """
pool.proxys.world:10000:user:pass
pool.proxys.world:10000:user:pass
"""
    urls = parse_proxy_file(content)
    assert len(urls) == 1


def test_parse_proxy_file_skips_invalid():
    content = """
http://good.example.com:8080
invalid_line_without_colon
pool.proxys.world:10001:user:pass
"""
    urls = parse_proxy_file(content)
    assert len(urls) == 2


def test_expand_port_range_basic():
    urls = expand_port_range("pool.proxys.world:10000-10002:user:pass")
    assert len(urls) == 3
    assert urls[0] == "http://user:pass@pool.proxys.world:10000"
    assert urls[1] == "http://user:pass@pool.proxys.world:10001"
    assert urls[2] == "http://user:pass@pool.proxys.world:10002"


def test_expand_port_range_large():
    urls = expand_port_range("pool.proxys.world:10000-10004:user283654o34626r400970:o7y50q")
    assert len(urls) == 5
    assert urls[0] == "http://user283654o34626r400970:o7y50q@pool.proxys.world:10000"
    assert urls[-1] == "http://user283654o34626r400970:o7y50q@pool.proxys.world:10004"


def test_expand_port_range_at_format():
    """Формат user:pass@host:start-end"""
    urls = expand_port_range("user283654o34626r400970:o7y50q@pool.proxys.world:10000-10002")
    assert len(urls) == 3
    assert urls[0] == "http://user283654o34626r400970:o7y50q@pool.proxys.world:10000"
    assert urls[1] == "http://user283654o34626r400970:o7y50q@pool.proxys.world:10001"
    assert urls[2] == "http://user283654o34626r400970:o7y50q@pool.proxys.world:10002"


def test_expand_port_range_at_format_large():
    urls = expand_port_range("user:pass@proxy.example.com:8000-8099")
    assert len(urls) == 100
    assert urls[0] == "http://user:pass@proxy.example.com:8000"
    assert urls[-1] == "http://user:pass@proxy.example.com:8099"


def test_expand_port_range_invalid():
    assert expand_port_range("invalid") == []
    assert expand_port_range("host:port:user") == []  # не 4 части
    assert expand_port_range("host:abc-def:user:pass") == []  # не числа


def test_parse_proxy_file_with_port_range():
    content = """
# Диапазон портов
pool.proxys.world:10000-10002:user:pass
# Обычный прокси
http://direct.example.com:8080
"""
    urls = parse_proxy_file(content)
    assert len(urls) == 4  # 3 из диапазона + 1 обычный
    assert "http://user:pass@pool.proxys.world:10000" in urls
    assert "http://user:pass@pool.proxys.world:10002" in urls
    assert "http://direct.example.com:8080" in urls
