"""Keep listing URLs on an existing page without losing their filters."""

from collections.abc import Iterable

from flask import Response, abort, redirect, request, url_for


def redirect_last_page(page: int, total_pages: int) -> Response | None:
    if page <= total_pages:
        return None
    arguments = request.args.to_dict()
    arguments.update(request.view_args or {})
    arguments["page"] = total_pages
    return redirect(url_for(request.endpoint, **arguments))


def text_filter(name: str = "q") -> str | None:
    """A listing's free-text filter, or None when it is absent or blank."""
    return request.args.get(name, "").strip() or None


def choice_filter(name: str, allowed: Iterable[str]) -> str | None:
    """A listing's closed-vocabulary filter, or None when it is absent.

    A value outside the vocabulary is a 400, as the stock listing's store
    filter is: no form offers it, so silently showing everything would hide
    a broken link.
    """
    raw = request.args.get(name, "").strip()
    if not raw:
        return None
    if raw not in allowed:
        abort(400)
    return raw


def active_filters(**filters: str | None) -> dict[str, str]:
    """The filters that are set, as URL arguments.

    Every link and form that leads back to a filtered list carries these, so
    paging and acting on a row return to the same filtered list (#538).
    """
    return {name: value for name, value in filters.items() if value}
