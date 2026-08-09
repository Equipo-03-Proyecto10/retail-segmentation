"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

State what this migration changes and why. A reader six weeks from now needs the
reason, not a restatement of the operations below. If it changes segment
assignment, RFM, or authentication, name the decision in
docs/data/postgresql-model.md that it follows or supersedes.

Definition of Done: a story that changes the database schema ships an updated
infra/sql/schema/verify_m1_schema.sql that passes.
"""

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
