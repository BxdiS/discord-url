import pytest

from dcurl.invite import InviteParseError, invite_url, parse_code, parse_watchlist


@pytest.mark.parametrize(
    "raw",
    [
        "discord.com/invite/abc123",
        "https://discord.com/invite/abc123",
        "https://discord.com/invite/abc123?event=42",
        "https://discord.com/invite/abc123#fragment",
        "http://www.discord.com/invite/abc123",
        "https://canary.discord.com/invite/abc123",
        "https://ptb.discord.com/invite/abc123",
        "discordapp.com/invite/abc123",
        "discord.gg/abc123",
        "https://discord.gg/abc123/",
        "discord.gg/invite/abc123",
        "abc123",
        "  discord.gg/abc123  ",
    ],
)
def test_parses_every_supported_form(raw):
    assert parse_code(raw) == "abc123"


def test_preserves_case_because_invite_codes_are_case_sensitive():
    assert parse_code("discord.gg/AbC-XyZ") == "AbC-XyZ"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "discord.gg/",
        "discord.com/abc123",  # у discord.com обязателен префикс /invite/
        "example.com/invite/abc123",  # чужой хост
        "discord.gg/a",  # короче двух символов
        "discord.gg/" + "a" * 33,  # длиннее 32
        "discord.gg/bad code",
        "ab$cd",
    ],
)
def test_rejects_bad_input(raw):
    with pytest.raises(InviteParseError):
        parse_code(raw)


def test_invite_url_is_canonical_short_form():
    assert invite_url("abc123") == "https://discord.gg/abc123"


def test_watchlist_skips_comments_and_dedupes():
    text = """
    # это комментарий

    discord.gg/abc123
    https://discord.com/invite/abc123
    discord.gg/xyz789
    """
    assert parse_watchlist(text) == ["abc123", "xyz789"]


def test_watchlist_error_points_at_the_offending_line():
    text = "discord.gg/good12\nне ссылка и не код\n"
    with pytest.raises(InviteParseError, match="строка 2"):
        parse_watchlist(text)


def test_empty_watchlist_is_not_an_error():
    assert parse_watchlist("# только комментарии\n\n") == []
