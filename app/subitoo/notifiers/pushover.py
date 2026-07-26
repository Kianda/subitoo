"""Pushover notification channel (the default, ships in-box).

Reads PUSHOVER_TOKEN / PUSHOVER_USER via the namespaced plugin config.
A contributor adds a new channel by copying this file's shape.
"""

from __future__ import annotations

import html

from curl_cffi import CurlMime, requests

from subitoo.core.models import Notification
from subitoo.notifiers.base import BaseNotifier
from subitoo.registry import register

API_URL = "https://api.pushover.net/1/messages.json"


def _fetch_image(url: str) -> tuple[bytes, str] | None:
    """Download the listing image so Pushover can attach it. Returns (bytes,
    content_type) or None — a broken/oversized image must never sink the notification,
    so any failure just drops the attachment and we send text-only."""
    try:
        # Pin the format: the CDN content-negotiates on Accept, so advertising only
        # jpeg/png keeps it from ever handing us AVIF (which Pushover won't render).
        r = requests.get(url, headers={"Accept": "image/jpeg,image/png"}, timeout=15)
        r.raise_for_status()
        data = r.content
        ctype = r.headers.get("Content-Type", "application/octet-stream")
        # Pushover caps attachments at ~5 MB; skip anything larger.
        return (data, ctype) if 0 < len(data) <= 5_000_000 else None
    except Exception:
        return None


@register("pushover")
class PushoverNotifier(BaseNotifier):
    def required_keys(self) -> list[str]:
        return ["token", "user"]

    def _format(self, n: Notification) -> tuple[str, str]:
        """Render the Pushover title + HTML body (`html=1`, see send). The title is
        the listing name, nothing else. The body is a row of colored badges
        (price · shipping), the location in italics, then a footer naming the query
        and source site. All dynamic text is HTML-escaped so a `&`/`<` can't break
        rendering."""
        sym = "€" if n.currency == "EUR" else html.escape(n.currency)
        title = n.title  # Pushover escapes the title field itself

        # Price badge: "?" when unknown; on a price change, append a colored delta
        # (green = dropped, red = rose) — the useful signal for a price-watcher.
        price_txt = "?" if n.price is None else f"{n.price:g}"
        badges = [f"<font color='#db00ba'>{price_txt} {sym}</font>"]
        if n.kind == "price_change" and n.price is not None and n.old_price is not None:
            delta = n.price - n.old_price
            color = "#d60000" if delta > 0 else "#00b53c"
            badges.append(f"<font color='{color}'>({delta:+g} {sym})</font>")
        if n.shipping_available:
            badges.append("<font color='#00b53c'>SHIPPING &#10003;</font>")

        message = " &#183; ".join(badges)
        if n.location:
            message += f"<br><i>{html.escape(n.location)}</i>"
        footer = f"<font color='#009dd6'>query:</font> '{html.escape(n.query_name)}'"
        if n.site:
            footer += f" ({html.escape(n.site)})"
        message += f"<br><br>{footer}"
        return title, message

    def _post(self, payload: dict[str, str], image: tuple[bytes, str] | None = None) -> None:
        """Isolated for easy mocking in tests. With an image we hand `curl_cffi` a
        `CurlMime` holding every field plus the attachment (it builds the multipart
        body); without one we send a plain urlencoded form via `data`."""
        if image is not None:
            data, ctype = image
            mp = CurlMime()
            for name, value in payload.items():
                mp.addpart(name=name, data=str(value).encode())
            mp.addpart(name="attachment", filename="image", content_type=ctype, data=data)
            resp = requests.post(API_URL, multipart=mp, timeout=15)
        else:
            resp = requests.post(API_URL, data=payload, timeout=15)
        if resp.status_code != 200:
            raise RuntimeError(f"pushover HTTP {resp.status_code}: {resp.content!r}")
        parsed = resp.json()
        if parsed.get("status") != 1:
            raise RuntimeError(f"pushover error: {parsed}")

    def send(self, n: Notification) -> None:
        self.validate_config()
        title, message = self._format(n)
        payload = {
            "token": self.conf["token"],
            "user": self.conf["user"],
            "title": title,
            "message": message,
            "html": "1",
        }
        if n.url:
            payload["url"] = n.url
            payload["url_title"] = "Open listing"
        image = _fetch_image(n.image_url) if n.image_url else None
        self._post(payload, image)
