"""Business logic.

A service answers a question or carries out an operation, and knows nothing
about HTTP: no request, no session, no template. That is what makes it testable
without a request context, and the rule is worth keeping even while the only
service in the package is as small as this one.
"""
