"""MOSAIQ — the retail segmentation web application.

One Flask monolith, organized by layers: `routes` speak HTTP, `services` hold
the business logic, `db` holds every SQL statement, and `templates` render the
HTML the browser receives. A layer calls the one below it and never the reverse.
"""
