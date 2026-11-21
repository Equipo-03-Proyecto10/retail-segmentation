"""Evidence collectors for the instructor's acceptance tests (F29, #888, #891).

They are command-line tools, not test cases: each run writes an evidence
directory (the requests and responses with their correlation ids, the rows,
the versions and a checks table) for a runbook in docs/evidence/ to link. The
unit tests of their pure parts are tests/test_acceptance_evidence.py.
"""
