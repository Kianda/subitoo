"""Plugin registry: @register + auto-discovery of sites/ and notifiers/ (Decision 4).

Contributors add one file under ``subitoo/sites/`` or ``subitoo/notifiers/`` and
decorate their class with ``@register("key")``. At boot, ``load_all()`` imports every
module in those packages, which runs the decorators and fills the registries.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from subitoo.notifiers.base import BaseNotifier
    from subitoo.sites.base import BaseSite

SITES: dict[str, type] = {}
NOTIFIERS: dict[str, type] = {}


def register(key: str):
    """Class decorator. Assigns ``key`` and files the class into the right registry.

    The target registry is chosen by the class's ``registry_kind`` attribute, set on
    ``BaseSite`` / ``BaseNotifier`` — so one decorator works for both.
    """

    def deco(cls):
        cls.key = key
        kind = getattr(cls, "registry_kind", None)
        if kind == "site":
            SITES[key] = cls
        elif kind == "notifier":
            NOTIFIERS[key] = cls
        else:
            raise TypeError(
                f"{cls!r} must subclass BaseSite or BaseNotifier to be @register'd"
            )
        return cls

    return deco


def _discover(package_name: str) -> None:
    package = importlib.import_module(package_name)
    for mod in pkgutil.iter_modules(package.__path__):
        if mod.name == "base":
            continue
        importlib.import_module(f"{package_name}.{mod.name}")


_loaded = False


def load_all() -> None:
    """Import all site/notifier modules so their @register decorators run. Idempotent."""
    global _loaded
    if _loaded:
        return
    _discover("subitoo.sites")
    _discover("subitoo.notifiers")
    _loaded = True


def get_site(key: str) -> "BaseSite":
    load_all()
    if key not in SITES:
        raise KeyError(f"unknown site adapter: {key!r} (have: {sorted(SITES)})")
    return SITES[key]()


def get_notifier(key: str) -> "BaseNotifier":
    load_all()
    if key not in NOTIFIERS:
        raise KeyError(f"unknown notifier: {key!r} (have: {sorted(NOTIFIERS)})")
    return NOTIFIERS[key]()


def site_for_url(url: str) -> str | None:
    """The site key whose adapter claims this search URL, or None if none does.

    Lets `query add` infer the site from the pasted URL instead of asking. Adapters
    opt in by setting ``url_host_pattern``; ones that aren't URL-based never match.
    """
    load_all()
    return next((k for k, cls in sorted(SITES.items()) if cls.claims_url(url)), None)


def site_keys() -> list[str]:
    load_all()
    return sorted(SITES)


def notifier_keys() -> list[str]:
    load_all()
    return sorted(NOTIFIERS)
