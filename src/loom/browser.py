"""Browser fill: open a page, read its form, fill it, and submit behind the human gate.

The transport is the boundary. Every url passes the host allowlist before the
browser moves (loopback is always allowed), and a request firewall re-checks
every request the page itself makes — navigation, script, or form — so page
code can never send one off the allowlist. A page that lands somewhere
disallowed is closed instead of being shown to the model. Fill only types into
the already-open page, and submit performs this module's single click. A
submit still waits for the approval gate upstream (docs/VISION.md).
"""

import logging
import re
from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urljoin, urlsplit

from loom.calendar import _is_loopback, redact_url  # one loopback rule and one url redaction rule for every transport
from loom.config import Settings

logger = logging.getLogger("loom")

_RENDER_CAP = 4_000
_PASSWORD_MASK = "***"
# autocomplete marks password-bearing controls regardless of their declared type.
_SENSITIVE_AUTOCOMPLETE = frozenset({"current-password", "new-password"})


@dataclass(frozen=True)
class BrowserField:
    """One form control: programmatic name, human label, kind, and current value.

    kind is the control type: text, email, tel, textarea, select, checkbox,
    hidden, or anything else an input declares. Password inputs and controls
    marked current/new-password autocomplete report _PASSWORD_MASK instead of
    their value ("" when empty), so no page state ever carries a password.
    """

    name: str
    label: str
    kind: str
    value: str


@dataclass(frozen=True)
class PageState:
    """What one open page shows the model: its url, title, and form fields."""

    url: str
    title: str
    fields: tuple[BrowserField, ...]


@dataclass(frozen=True)
class SubmitOutcome:
    """What a submit produced: the result page's url, whether the site accepted the form, one detail line."""

    url: str
    accepted: bool
    detail: str


class BrowserTransport(Protocol):
    """How the browser tools reach a page."""

    def open(self, url: str) -> PageState: ...
    def fill(self, fields: dict[str, str]) -> PageState: ...
    def submit(self) -> SubmitOutcome: ...
    def pending_approval(self) -> dict: ...


def _parse_allowed_hosts(raw: str) -> frozenset[str]:
    """LOOM_BROWSER_ALLOWED_HOSTS as a host set: comma-separated, trimmed, lowercased."""
    return frozenset(host.strip().lower() for host in raw.split(",") if host.strip())


def _ensure_allowed(url: str, allowed_hosts: frozenset[str]) -> None:
    """Refuse any url whose host is neither loopback nor listed in LOOM_BROWSER_ALLOWED_HOSTS."""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        host = ""
    if not host or not (_is_loopback(host) or host in allowed_hosts):
        raise ValueError(
            f"host {host or url!r} is not in LOOM_BROWSER_ALLOWED_HOSTS; "
            "loopback hosts are always allowed and every other host must be listed"
        )


def render_page_state(state: PageState, cap: int = _RENDER_CAP) -> str:
    """The page as the model sees it: url, title, one line per field, and the submit path.

    The text is capped with an explicit truncation marker so a huge form cannot
    flood the context.
    """
    lines = [f"url: {state.url}", f"title: {state.title}"]
    lines += [f"- {f.name} [{f.kind}] {f.label!r} = {f.value!r}" for f in state.fields]
    lines.append("submit: browser_submit triggers the page's submit control; it waits for human approval")
    text = "\n".join(lines)
    if len(text) > cap:
        text = text[:cap].rstrip() + f"\n…[truncated at {cap} characters]"
    return text


class PlaywrightBrowserTransport:
    """Headless chromium through playwright, started lazily on first use.

    playwright is imported only when a page is actually needed, so the registry
    builds without it. A route firewall passes every request the page makes
    through the host allowlist and aborts the rest, so page scripts cannot send
    anything around the checks that run before open and submit. A navigation
    that lands off the allowlist closes the page: off-site content is never
    returned to the model. The browser lives for the transport's lifetime;
    close() ends it.
    """

    _BUTTON_INPUT_KINDS = frozenset({"submit", "button", "image", "reset"})

    def __init__(self, settings: Settings) -> None:
        self._allowed = _parse_allowed_hosts(settings.browser_allowed_hosts)
        self._pw = None
        self._browser = None
        self._page = None
        self._blocked: list[str] = []

    def open(self, url: str) -> PageState:
        _ensure_allowed(url, self._allowed)
        page = self._driver()
        try:
            page.goto(url)
        except Exception as exc:  # noqa: BLE001 - one readable failure shape for every navigation cause
            self._page = None
            page.close()
            raise RuntimeError(f"browser could not open {url}: {type(exc).__name__}") from None
        self._ensure_landing_allowed()
        try:
            return self._read_state()
        except Exception as exc:  # noqa: BLE001 - same failure shape while reading the rendered page
            raise RuntimeError(f"browser could not read the page at {url}: {type(exc).__name__}") from None

    def fill(self, fields: dict[str, str]) -> PageState:
        page = self._require_page()
        for name, value in fields.items():
            found = self._locate_control(page, str(name))
            if found is None:
                known = [f.name for f in self._read_state().fields]
                raise ValueError(f"no field named {name!r}; the page has: {', '.join(known) or '(none)'}")
            el, kind = found
            self._set(str(name), el, kind, str(value))
        self._ensure_landing_allowed()
        return self._read_state()

    def submit(self) -> SubmitOutcome:
        """The module's one click, behind three gates: the url the click will
        use passes the allowlist before the click, the firewall watches the
        request the page actually sends, and the landing is re-checked — a
        blocked submission raises instead of reading as accepted."""
        page = self._require_page()
        button = self._submit_button(page)
        _ensure_allowed(self._submit_target(page, button), self._allowed)
        before = len(self._blocked)
        try:
            button.click()
            page.wait_for_load_state()
        except Exception as exc:  # noqa: BLE001 - one readable failure shape for the gated click
            if len(self._blocked) == before:
                raise RuntimeError(f"browser submit failed: {type(exc).__name__}") from None
        if len(self._blocked) > before:
            raise RuntimeError("the form submission was blocked by the host allowlist; nothing was sent")
        self._ensure_landing_allowed()
        return SubmitOutcome(url=page.url, accepted=True, detail=self._first_line(page))

    def _submit_target(self, page, button) -> str:
        """The url clicking this control sends to: its own formaction, else its
        owning form's action, else this page.

        The owning form is resolved relative to the control, so an earlier form
        in the document cannot misdirect the check, and a rewrite the page
        applies on submit is not visible here — the firewall is the ground
        truth for what is actually sent.
        """
        own = button.get_attribute("formaction")
        if own:
            return urljoin(page.url, own)
        owner = button.locator("xpath=ancestor::form[1]").first
        action = owner.get_attribute("action") if owner.count() else ""
        return urljoin(page.url, action) if action else page.url

    def pending_approval(self) -> dict:
        """The live page as the gate's approval payload for browser_submit."""
        if self._page is None:
            return {"state": "no page is open"}
        state = self._read_state()
        return {"url": state.url, "fields": {f.name: f.value for f in state.fields}}

    def close(self) -> None:
        """Stop chromium if this transport started one; the Protocol does not require it."""
        self._page = None
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._pw is not None:
            self._pw.stop()
            self._pw = None

    def _driver(self):
        if self._page is not None:
            return self._page
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError('browser support is not installed; run: pip install -e ".[browser]"') from None
        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
            self._page = self._browser.new_page()
            self._page.route("**/*", self._firewall)
            self._page.set_default_timeout(10_000)
        except Exception as exc:  # noqa: BLE001 - a half-started browser must read as one launch failure
            self.close()
            raise RuntimeError(f"browser launch failed: {type(exc).__name__}") from None
        return self._page

    def _firewall(self, route) -> None:
        """Every request the page makes crosses the allowlist; refused ones are recorded, logged, aborted."""
        url = route.request.url
        try:
            try:
                _ensure_allowed(url, self._allowed)
            except ValueError:
                self._blocked.append(url)
                logger.warning("browser request blocked by the host allowlist", extra={"host": redact_url(url)})
                route.abort("aborted")
                return
            route.continue_()
        except Exception:  # noqa: BLE001 - a route handler must fail closed, never crash the browser
            with suppress(Exception):  # the request may already be settled
                route.abort("aborted")

    def _ensure_landing_allowed(self) -> None:
        """A redirect or script navigation that left the allowlist closes the page; the model sees nothing.

        chromium follows redirect responses in its network stack, before the
        route firewall can see them, so this check is the only gate on where a
        navigation actually landed.
        """
        page = self._page
        if page is None:
            return
        try:
            _ensure_allowed(page.url, self._allowed)
        except ValueError:
            url = page.url
            self._blocked.append(url)
            logger.warning("browser request blocked by the host allowlist", extra={"host": redact_url(url)})
            self._page = None
            page.close()
            raise RuntimeError(
                f"the browser landed outside the host allowlist at {redact_url(url)}; the page was closed"
            ) from None

    def _require_page(self):
        if self._page is None:
            raise RuntimeError("no page is open; call browser_open first")
        return self._page

    def _read_state(self) -> PageState:
        page = self._page
        fields = tuple(self._field(el, kind) for el, kind in self._controls(page))
        return PageState(url=page.url, title=page.title(), fields=fields)

    def _controls(self, page) -> list:
        controls = [(el, (el.get_attribute("type") or "text").lower()) for el in page.locator("input").all()]
        controls = [(el, kind) for el, kind in controls if kind not in self._BUTTON_INPUT_KINDS]
        controls += [(el, "textarea") for el in page.locator("textarea").all()]
        controls += [(el, "select") for el in page.locator("select").all()]
        return controls

    def _field(self, el, kind: str) -> BrowserField:
        name = el.get_attribute("name") or el.get_attribute("id") or ""
        sensitive = kind == "password" or (el.get_attribute("autocomplete") or "").strip().lower() in _SENSITIVE_AUTOCOMPLETE
        if sensitive:
            # The page's value never leaves the browser; the mask alone says "filled".
            value = _PASSWORD_MASK if el.input_value() else ""
        elif kind == "checkbox":
            value = "true" if el.is_checked() else "false"
        else:
            value = el.input_value()
        return BrowserField(name=name, label=self._label_for(el), kind=kind, value=value)

    def _label_for(self, el) -> str:
        el_id = el.get_attribute("id")
        if el_id:
            for label in el.page.locator("label").all():
                if label.get_attribute("for") == el_id:
                    return (label.text_content() or "").strip()
        wrapped = el.locator("xpath=ancestor::label[1]")
        if wrapped.count():
            return (wrapped.first.text_content() or "").strip()
        return el.get_attribute("aria-label") or el.get_attribute("placeholder") or ""

    def _locate_control(self, page, name: str):
        controls = self._controls(page)
        for el, kind in controls:
            if name in (el.get_attribute("name"), el.get_attribute("id")):
                return el, kind
        for el, kind in controls:
            if self._label_for(el) == name:
                return el, kind
        return None

    def _set(self, name: str, el, kind: str, value: str) -> None:
        if kind == "select":
            self._select(name, el, value)
            return
        try:
            if kind == "checkbox":
                if value.strip().lower() in ("true", "1", "on", "yes"):
                    el.check()
                else:
                    el.uncheck()
            else:
                el.fill(value)
        except Exception as exc:  # noqa: BLE001 - one readable failure shape while filling
            raise RuntimeError(f"could not set field {name!r}: {type(exc).__name__}") from None

    def _select(self, name: str, el, value: str) -> None:
        options = {o.get_attribute("value"): (o.text_content() or "").strip() for o in el.locator("option").all()}
        labels = {label: val for val, label in options.items()}
        if value in options:
            pick = {"value": value}
        elif value in labels:
            pick = {"label": value}
        else:
            shown = ", ".join(sorted(key for key in options if key)) or "(none)"
            raise ValueError(f"{name!r} has no option {value!r}; options: {shown}")
        try:
            el.select_option(**pick)
        except Exception as exc:  # noqa: BLE001 - a matched option can still fail to select
            raise RuntimeError(f"could not select {value!r} in {name!r}: {type(exc).__name__}") from None

    def _submit_button(self, page):
        for selector in ("button[type='submit']", "input[type='submit']"):
            located = page.locator(selector)
            if located.count():
                return located.first
        named = page.locator("button[type='button']").filter(has_text=re.compile("submit", re.IGNORECASE))
        if named.count():
            return named.first
        raise RuntimeError("no submit control found on the page; nothing was sent")

    def _first_line(self, page) -> str:
        heading = page.locator("h1").first
        if heading.count():
            text = (heading.text_content() or "").strip()
            if text:
                return text
        title = page.title().strip()
        if title:
            return title
        body = page.locator("body").first.text_content() or ""
        return next((line.strip() for line in body.splitlines() if line.strip()), "")
