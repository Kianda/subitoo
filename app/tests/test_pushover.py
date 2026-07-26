"""Unit test for the Pushover notifier's message rendering — no network.

Guards the HTML layout of ``_format`` (title, colored badges, footer, escaping)
so a refactor can't silently change what lands on the phone.
"""

from subitoo.core.models import Notification
from subitoo.notifiers.pushover import PushoverNotifier


def _fmt(**kw):
    base = dict(kind="new", title="Item", url="", query_name="q", site="subito")
    return PushoverNotifier()._format(Notification(**{**base, **kw}))


def test_title_is_listing_title_only():
    title, _ = _fmt(title="Lenovo Legion Slim 5")
    assert title == "Lenovo Legion Slim 5"  # no emoji / prefix / query name


def test_new_listing_badges_location_and_footer():
    _, msg = _fmt(title="Lenovo", price=940.0, shipping_available=True,
                  location="Lagundo (BZ)", query_name="lenovo laptop", site="subito")
    assert "<font color='#db00ba'>940 €</font>" in msg          # price, magenta
    assert "<font color='#00b53c'>SHIPPING &#10003;</font>" in msg  # shipping, green
    assert "<i>Lagundo (BZ)</i>" in msg                          # location, italic
    assert "query:</font> 'lenovo laptop' (subito)" in msg       # query + site footer


def test_missing_price_shows_question_mark():
    _, msg = _fmt(price=None)
    assert "<font color='#db00ba'>? €</font>" in msg


def test_price_change_delta_green_on_drop():
    _, msg = _fmt(kind="price_change", price=940.0, old_price=1090.0)
    assert "<font color='#db00ba'>940 €</font>" in msg
    assert "<font color='#00b53c'>(-150 €)</font>" in msg        # dropped -> green


def test_price_change_delta_red_on_rise():
    _, msg = _fmt(kind="price_change", price=940.0, old_price=800.0)
    assert "<font color='#d60000'>(+140 €)</font>" in msg        # rose -> red


def test_dynamic_text_is_escaped():
    _, msg = _fmt(location="A & <b>B</b>", query_name="c < d")
    assert "A &amp; &lt;b&gt;B&lt;/b&gt;" in msg
    assert "'c &lt; d'" in msg


def test_non_eur_currency_falls_back_to_code():
    _, msg = _fmt(price=10.0, currency="USD")
    assert "10 USD" in msg
