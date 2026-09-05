"""Data access.

Every SQL statement the application runs belongs in this package, and every one
of them is parameterized — no string interpolation into SQL, anywhere, ever.
Routes and services call functions from here; they never carry SQL themselves.

DDL is not here either: the schema is `sql/01_schema.sql` and nothing else
creates or alters a table.

The connection itself arrives with F3-02, which reads `DATABASE_URL` from the
environment. Until then this package is the layer's boundary and holds no code:
the application does not talk to PostgreSQL yet.
"""
