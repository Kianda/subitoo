"""The `query add` wizard asks only the universal filters a site declares.

An adapter narrows `BaseSite.universal_filters` when its own search URL already
expresses a filter (Vinted carries price server-side). That is a PROMPTING choice:
the core still applies every stored filter on every site (SPEC Decision 5), so these
tests pin the questions asked, never what the filtering does.
"""

from __future__ import annotations

import pytest

from subitoo import cli, registry
from subitoo.core.models import UNIVERSAL_FILTERS
from subitoo.sites.base import BaseSite


@pytest.fixture
def answers(monkeypatch):
    """Drive `typer.prompt`, recording every label it was asked with.

    Returns the label list; the prompt hands back each field's own default, i.e. what
    a user pressing Enter through the wizard would get.
    """
    asked: list[str] = []

    def fake_prompt(text, default="", show_default=True):
        asked.append(text.strip())
        return default

    monkeypatch.setattr(cli.typer, "prompt", fake_prompt)
    return asked


def test_default_adapter_is_asked_everything(answers):
    """Say nothing and you get the full set — narrowing is opt-in per adapter."""
    cli._prompt_filters(registry.get_site("fake"))
    assert answers == [cli._FILTER_PROMPTS[n][0] for n in UNIVERSAL_FILTERS]


def test_vinted_is_only_asked_for_the_title_regex(answers):
    cli._prompt_filters(registry.get_site("vinted"))
    assert answers == ["title_include (regex)", "title_exclude (regex)"]


def test_subito_is_asked_everything(answers):
    """Subito's price filter only appears once a category is picked, so its URL can't
    carry one — the wizard has to ask."""
    cli._prompt_filters(registry.get_site("subito"))
    assert "price_min" in answers and "shipping (any/yes/no)" in answers


def test_skipped_filters_fall_back_to_their_defaults(answers):
    """A filter the wizard skips is off, not junk: no price bound, shipping 'any'."""
    filters = cli._prompt_filters(registry.get_site("vinted"))
    assert filters.price_min is None
    assert filters.price_max is None
    assert filters.shipping == "any"


def test_answers_land_on_the_right_fields(monkeypatch):
    replies = {
        "price_min": "75",
        "price_max": "150",
        "shipping (any/yes/no)": "yes",
        "title_include (regex)": "DS ?124",
        "title_exclude (regex)": "DS218j",
    }
    monkeypatch.setattr(cli.typer, "prompt",
                        lambda text, default="", show_default=True: replies[text.strip()])

    filters = cli._prompt_filters(registry.get_site("fake"))
    assert (filters.price_min, filters.price_max) == (75.0, 150.0)
    assert filters.shipping == "yes"
    assert filters.title_include == "DS ?124"
    assert filters.title_exclude == "DS218j"


def test_every_adapter_declares_only_real_filter_names():
    """A typo in `universal_filters` would silently drop a prompt, not raise — so the
    contract is checked here instead, for every installed adapter."""
    registry.load_all()
    for key in registry.site_keys():
        unknown = set(registry.SITES[key].universal_filters) - set(UNIVERSAL_FILTERS)
        assert not unknown, f"{key}: unknown universal filter(s) {sorted(unknown)}"


def test_the_prompt_table_covers_every_filter():
    """`_FILTER_PROMPTS` and `UNIVERSAL_FILTERS` must not drift apart — a filter added
    to the model with no prompt entry would raise KeyError mid-wizard."""
    assert set(cli._FILTER_PROMPTS) == set(UNIVERSAL_FILTERS)
    assert set(BaseSite.universal_filters) == set(UNIVERSAL_FILTERS)


# --------------------------------------------------------------- edit prefill

def test_edit_offers_the_current_values_as_defaults(monkeypatch):
    """Enter through an edit keeps what's stored — the prompt hands back its default."""
    from subitoo.core.models import Filters

    monkeypatch.setattr(cli.typer, "prompt",
                        lambda text, default="", show_default=True: default)
    current = Filters(title_include="meta|quest", title_exclude=r"3\s?s\b")
    assert cli._prompt_filters(registry.get_site("vinted"), current=current) == current


def test_edit_clears_a_filter_with_the_clear_sentinel(monkeypatch):
    """An empty line means 'keep' once a default is shown, so clearing needs its own
    token — otherwise a filter could be set but never turned back off."""
    from subitoo.core.models import Filters

    # typer hands back the default on an empty line, so "keep" is simply absence here
    replies = {"title_include (regex)": cli.CLEAR}
    monkeypatch.setattr(cli.typer, "prompt",
                        lambda text, default="", show_default=True:
                        replies.get(text.strip(), default))
    current = Filters(title_include="meta|quest", title_exclude="3s")
    out = cli._prompt_filters(registry.get_site("vinted"), current=current)
    assert out.title_include is None   # cleared
    assert out.title_exclude == "3s"   # Enter kept it


def test_edit_does_not_reset_a_filter_the_site_never_asks_about(monkeypatch):
    """Vinted skips the price prompts, but a price bound set via --from-json is the
    user's deliberate choice — skipping the question must not silently drop it."""
    from subitoo.core.models import Filters

    monkeypatch.setattr(cli.typer, "prompt",
                        lambda text, default="", show_default=True: default)
    current = Filters(price_max=150.0, shipping="yes", title_include="meta")
    out = cli._prompt_filters(registry.get_site("vinted"), current=current)
    assert out.price_max == 150.0
    assert out.shipping == "yes"
