"""Assign UCDP events coded with GW 678 (Yemen) and 345 (Serbia/Yugoslavia) to their countries.

These events were imported without a country because the registry only knew GW 680 / 340, so verified deaths in
Yemen were missing from severity, labels and early-warning inputs.

Revision ID: 0004
Revises: 0003
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for name, iso3 in (("Yemen (North Yemen)", "YEM"), ("Serbia (Yugoslavia)", "SRB")):
        op.execute(
            "UPDATE ucdp_events SET country_id = (SELECT id FROM countries WHERE iso3 = '%s') "
            "WHERE country_id IS NULL AND country_name = '%s'" % (iso3, name.replace("'", "''"))
        )


def downgrade() -> None:
    pass
