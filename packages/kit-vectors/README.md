# Python kit vectors

`jwks.json` contains only the public members of a throwaway RSA key.
`tokens.json` contains valid placeholder-role tokens and one token for each
authentication failure. `expected.json` records the status and error code for
those failures and the stopped-database case.

Regenerate all three files from the repository root:

```sh
cd packages/kit-py
uv run python ../../tests/kit_vectors/generate.py
```

The generator creates the private key in memory and never writes it.
