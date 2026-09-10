"""ntfy notification channel (https://ntfy.sh, or your own server).

Reads NTFY_TOPIC / NTFY_URL / NTFY_TOKEN via the namespaced plugin config. Only the
topic is required — the URL defaults to the public ntfy.sh and the token is only needed
for a protected topic.

Three things differ from the Pushover channel and shape this file:

  no markup   ntfy has no HTML and no color, so the rendering is plain text + one emoji
              tag. Nothing needs escaping (unlike Pushover's HTML body), which is why
              there's no `html.escape` here.
  attachments are a URL, not an upload — `attach` hands the client the image link and it
              fetches it, so this channel needs no download, content-type pinning or
              size cap.
  JSON body   ntfy also accepts title/click/etc. as HTTP *headers*, but headers mangle
              non-ASCII and marketplace titles are full of `é`/`à`/`€`. Publishing a
              JSON body to the server root keeps them byte-exact.

⚠️ An ntfy topic is a shared secret: anyone who knows the name can read (and post to)
it. Use a long random topic, or a server with auth.
"""

from __future__ import annotations

from curl_cffi import requests

from subitoo.core.models import Notification
from subitoo.notifiers.base import BaseNotifier
from subitoo.registry import register

DEFAULT_URL = "https://ntfy.sh"

#: One emoji tag per event, so the phone shows at a glance what happened. ntfy renders
#: a known emoji shortcode in front of the title.
TAGS = {"new": "sparkles", "drop": "chart_with_downwards_trend",
        "rise": "chart_with_upwards_trend"}


@register("ntfy")
class NtfyNotifier(BaseNotifier):
    def required_keys(self) -> list[str]:
        return ["topic"]

    def _format(self, n: Notification) -> tuple[str, str, list[str]]:
        """Render the ntfy title, plain-text body and emoji tag.

        Mirrors the Pushover layout so both channels say the same thing: the listing
        title alone as the title, then price (with the delta on a price change) and
        shipping on one line, the location, and a footer naming the query and site.
        """
        sym = "€" if n.currency == "EUR" else n.currency
        price_txt = "?" if n.price is None else f"{n.price:g}"
        parts = [f"{price_txt} {sym}"]
        tag = TAGS["new"]
        if n.kind == "price_change" and n.price is not None and n.old_price is not None:
            delta = n.price - n.old_price
            parts.append(f"({delta:+g} {sym})")
            tag = TAGS["rise"] if delta > 0 else TAGS["drop"]
        if n.shipping_available:
            parts.append("SHIPPING ✓")

        message = " · ".join(parts)
        if n.location:
            message += f"\n{n.location}"
        footer = f"query: '{n.query_name}'"
        if n.site:
            footer += f" ({n.site})"
        message += f"\n\n{footer}"
        return n.title, message, [tag]

    def _payload(self, n: Notification) -> dict:
        title, message, tags = self._format(n)
        payload: dict = {
            "topic": self.conf["topic"],
            "title": title,
            "message": message,
            "tags": tags,
        }
        if n.url:
            payload["click"] = n.url  # tapping the notification opens the listing
        if n.image_url:
            payload["attach"] = n.image_url  # fetched by the client, not by us
        return payload

    def _post(self, payload: dict) -> None:
        """Isolated for easy mocking in tests (same shape as the Pushover channel)."""
        base = (self.conf.get("url") or DEFAULT_URL).rstrip("/")
        headers = {}
        if self.conf.get("token"):
            headers["Authorization"] = f"Bearer {self.conf['token']}"
        resp = requests.post(f"{base}/", json=payload, headers=headers, timeout=15)
        if resp.status_code != 200:
            raise RuntimeError(f"ntfy HTTP {resp.status_code}: {resp.content!r}")
        # A 200 from something that isn't ntfy (a captive portal, a misrouted proxy)
        # would otherwise look like a delivered notification.
        try:
            published = resp.json()
        except ValueError as e:
            raise RuntimeError(f"ntfy: expected JSON, got {resp.content[:200]!r}") from e
        if not published.get("id"):
            raise RuntimeError(f"ntfy error: {published}")

    def send(self, n: Notification) -> None:
        self.validate_config()
        self._post(self._payload(n))
