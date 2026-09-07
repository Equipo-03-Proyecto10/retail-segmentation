"""Keep listing URLs on an existing page without losing their filters."""

from flask import Response, redirect, request, url_for


def redirect_last_page(page: int, total_pages: int) -> Response | None:
    if page <= total_pages:
        return None
    arguments = request.args.to_dict()
    arguments.update(request.view_args or {})
    arguments["page"] = total_pages
    return redirect(url_for(request.endpoint, **arguments))
