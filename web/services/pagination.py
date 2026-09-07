"""Pagination arithmetic shared by HTTP listings and the audit service."""


def page_count(total: int, per_page: int) -> int:
    return max(1, (total + per_page - 1) // per_page)
