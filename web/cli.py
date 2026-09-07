"""Commands run on the instance, not by a visitor (F4-06).

`sql/02_seed_30_per_table.sql` creates thirty demonstration accounts, all with
the password `Password123!`. That is correct for local work and for the
demonstration, and it is a hole on a published host: the credentials are in
this repository, on a public branch. No story before F4-06 (#107) covered the
transition, and F4-02 (#70) makes it harder rather than easier — the seeded
administrator cannot simply be joined by a real one.

    flask --app web.app provision-administrator \\
        --name "Real Person" --email person@udem.edu --deactivate-demo-accounts

The password is read from `MOSAIQ_ADMIN_PASSWORD` or asked for at the prompt,
and from nowhere else. It is never a command-line argument — that would put it
in the shell history and in `ps` — and never a file in this repository.
"""

from __future__ import annotations

import os
from getpass import getpass

import click
from flask import Flask
from flask.cli import with_appcontext

from web.db import get_connection
from web.db.users import (
    count_active_demonstration_accounts,
    count_administrators,
    get_user_by_email,
    list_accounts_with_roles,
)
from web.services.users import (
    MINIMUM_PASSWORD_LENGTH,
    DuplicateEmailError,
    SingleAdministratorError,
)
from web.services.users import (
    provision_administrator as provision_account,
)
from web.services.users import (
    rotate_password as rotate_account_password,
)

PASSWORD_VARIABLE = "MOSAIQ_ADMIN_PASSWORD"

# The password this repository publishes. Refused by name: the whole point of
# the procedure is that the deployed system stops being reachable with it.
PUBLISHED_PASSWORD = "Password123!"  # noqa: S105 - refused, never assigned


class PasswordRefused(click.ClickException):
    """The password offered is not one this command will install."""


def _read_password() -> str:
    """Take the password from the environment, or ask for it.

    Two sources and no third: an operator exporting it from a secret store, and
    a person typing it. `getpass` does not echo, and neither path lets the
    value reach the shell history, `ps`, or a file under version control.
    """
    from_environment = os.environ.get(PASSWORD_VARIABLE)
    if from_environment:
        password = from_environment
    else:
        password = getpass(
            f"Password for the administrator ({PASSWORD_VARIABLE} "
            "is unset, so asking): "
        )
        if password != getpass("Again, to be sure: "):
            raise PasswordRefused("The two passwords do not match.")

    if password == PUBLISHED_PASSWORD:
        raise PasswordRefused(
            "That is the password this repository publishes for the seeded "
            "accounts. The instance must not be reachable with it."
        )
    if len(password) < MINIMUM_PASSWORD_LENGTH:
        raise PasswordRefused(
            f"The password must be at least {MINIMUM_PASSWORD_LENGTH} characters."
        )
    return password


@click.command("provision-administrator")
@click.option("--name", required=True, help="The person's name, as pages will show it.")
@click.option("--email", required=True, help="The address they will sign in with.")
@click.option(
    "--deactivate-demo-accounts",
    is_flag=True,
    help="Also deactivate every seeded @mosaiq-demo.com account.",
)
@with_appcontext
def provision_administrator(
    name: str, email: str, deactivate_demo_accounts: bool
) -> None:
    """Make a real account the one administrator of this instance."""
    password = _read_password()
    connection = get_connection()

    if get_user_by_email(connection, email) is not None:
        raise click.ClickException(
            f"{email} already has an account. Choose another address, or "
            "rotate that account's password instead."
        )

    before = count_administrators(connection)
    try:
        deactivated = provision_account(
            connection,
            name=name,
            email=email,
            password=password,
            deactivate_demo_accounts=deactivate_demo_accounts,
        )
    except (SingleAdministratorError, DuplicateEmailError) as refusal:
        raise click.ClickException(str(refusal)) from refusal

    click.echo(f"{email} is now the administrator (there were {before} before).")
    if deactivated:
        click.echo(f"Deactivated {len(deactivated)} demonstration accounts.")
    remaining = count_active_demonstration_accounts(connection)
    click.echo(
        f"Administrators: {count_administrators(connection)}. "
        f"Demonstration accounts that can still sign in: {remaining}."
    )
    if remaining:
        click.echo(
            "Those accounts still take the password published in this "
            "repository. Re-run with --deactivate-demo-accounts to close them."
        )


@click.command("rotate-password")
@click.option("--email", required=True, help="The account whose password changes.")
@with_appcontext
def rotate_password(email: str) -> None:
    """Give an existing account a new password, read the same way."""
    password = _read_password()
    connection = get_connection()

    if get_user_by_email(connection, email) is None:
        raise click.ClickException(f"No account exists for {email}.")

    rotate_account_password(connection, email, password)
    click.echo(f"The password for {email} has been changed.")


@click.command("account-report")
@with_appcontext
def account_report() -> None:
    """Say who can sign in to this instance, and with what."""
    connection = get_connection()
    rows = list_accounts_with_roles(connection)

    active = [row for row in rows if row[2]]
    click.echo(f"{len(rows)} accounts, {len(active)} of them active.")
    for role_code, email, _is_active in active:
        published = (
            " ← published password" if email.endswith("@mosaiq-demo.com") else ""
        )
        click.echo(f"  {role_code:<18} {email}{published}")
    click.echo(
        f"Demonstration accounts that can still sign in: "
        f"{count_active_demonstration_accounts(connection)}"
    )


def register_commands(app: Flask) -> None:
    """Attach the instance commands to `flask --app web.app`."""
    app.cli.add_command(provision_administrator)
    app.cli.add_command(rotate_password)
    app.cli.add_command(account_report)
