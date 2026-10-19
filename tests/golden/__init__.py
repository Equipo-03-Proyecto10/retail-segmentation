"""Golden fixtures of the web's ported rules (F13-11, ADR-0045).

The one place outside web/ that may import web/. It runs the web's own code on
the seed database, inside transactions that always roll back, and writes what
came out as JSON for the services that port those rules. See README.md.
"""
