"""Unit test for the ntfy notifier's rendering + payload — no network.

Mirrors tests/test_pushover.py: guards what lands on the phone so a refactor can't
silently change it. Also pins the two ntfy-specific choices — the image goes as a URL
for the client to fetch, and everything travels in the JSON body (not headers, which
mangle non-ASCII titles).
"""

import pytest

from subitoo.core.models import Notification
from subitoo.notifiers.ntfy import NtfyNotifier


def _notifier(**conf):
    n = NtfyNotifier()
    n.conf = {"topic": "t", **conf}   # bypass env: for_plugin reads os.environ
    return n


def _fmt(**kw):
    base = dict(kind="new", title="Item", url="", query_name="q", site="subito")
    return _notifier()._format(Notification(**{**base, **kw}))


def _payload(**kw):
    base = dict(kind="new", title="Item", url="", query_name="q", site="subito")
    return _notifier()._payload(Notification(**{**base, **kw}))


def test_title_is_listing_title_only():
    title, _, _ = _fmt(title="Lenovo Legion Slim 5")
    assert title == "Lenovo Legion Slim 5"


def test_new_listing_price_shipping_location_and_footer():
    _, msg, tags = _fmt(title="Lenovo", price=940.0, shipping_available=True,
                        location="Lagundo (BZ)", query_name="lenovo laptop", site="subito")
    assert msg == "940 € · SHIPPING ✓\nLagundo (BZ)\n\nquery: 'lenovo laptop' (subito)"
    assert tags == ["sparkles"]


def test_missing_price_shows_question_mark():
    _, msg, _ = _fmt(price=None)
    assert msg.startswith("? €")


def test_price_change_tags_a_drop_and_a_rise():
    _, msg, tags = _fmt(kind="price_change", price=940.0, old_price=1090.0)
    assert "940 € · (-150 €)" in msg
    assert tags == ["chart_with_downwards_trend"]

    _, msg, tags = _fmt(kind="price_change", price=940.0, old_price=800.0)
    assert "(+140 €)" in msg
    assert tags == ["chart_with_upwards_trend"]


def test_non_eur_currency_falls_back_to_code():
    _, msg, _ = _fmt(price=10.0, currency="USD")
    assert msg.startswith("10 USD")


def test_no_markup_escaping_is_applied():
    """ntfy renders plain text — unlike the Pushover channel, an `&` or `<` must arrive
    verbatim rather than HTML-escaped."""
    _, msg, _ = _fmt(location="A & <b>B</b>", query_name="c < d")
    assert "A & <b>B</b>" in msg
    assert "'c < d'" in msg


def test_payload_carries_click_and_attaches_the_image_by_url():
    p = _payload(title="NAS", price=170.0, url="https://x.test/items/1",
                 image_url="https://cdn.test/a.jpg")
    assert p["topic"] == "t"
    assert p["click"] == "https://x.test/items/1"
    # the image is a URL for the client to fetch — we never download it
    assert p["attach"] == "https://cdn.test/a.jpg"


def test_payload_omits_click_and_attach_when_absent():
    p = _payload(price=1.0)
    assert "click" not in p and "attach" not in p


def test_non_ascii_survives_in_the_body():
    """The reason we publish JSON instead of ntfy's header form."""
    p = _payload(title="NAS Synology DS720+ avec HDD d’origine", price=500.0)
    assert p["title"] == "NAS Synology DS720+ avec HDD d’origine"
    assert "500 €" in p["message"]


def test_missing_topic_fails_fast():
    n = NtfyNotifier()
    n.conf = {}
    with pytest.raises(RuntimeError, match="NTFY_TOPIC"):
        n.validate_config()


def test_post_targets_the_configured_server_with_the_token(monkeypatch):
    seen = {}

    class Resp:
        status_code = 200
        def json(self): return {"id": "abc"}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, json=json, headers=headers)
        return Resp()

    monkeypatch.setattr("subitoo.notifiers.ntfy.requests.post", fake_post)
    n = _notifier(url="https://ntfy.example.com/", token="tk_secret")
    n.send(Notification(kind="new", title="X", url="", query_name="q", price=1.0))
    assert seen["url"] == "https://ntfy.example.com/"       # trailing slash normalized
    assert seen["headers"]["Authorization"] == "Bearer tk_secret"
    assert seen["json"]["topic"] == "t"


def test_post_rejects_a_200_that_isnt_ntfy(monkeypatch):
    class Resp:
        status_code = 200
        content = b"<html>captive portal</html>"
        def json(self): raise ValueError("not json")

    monkeypatch.setattr("subitoo.notifiers.ntfy.requests.post",
                        lambda *a, **k: Resp())
    with pytest.raises(RuntimeError, match="expected JSON"):
        _notifier().send(Notification(kind="new", title="X", url="", query_name="q"))
