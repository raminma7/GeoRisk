"""Escalation models replace the conflict-onset model.

Adds ``ml_predictions.current_tier`` and removes the old model's stored predictions and description.

Revision ID: 0005
Revises: 0004
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ml_predictions", sa.Column("current_tier", sa.String(length=20), nullable=True))
    op.execute("DELETE FROM ml_predictions WHERE model_name = 'conflict_onset'")
    op.execute("DELETE FROM app_settings WHERE key = 'ml_model_info'")


def downgrade() -> None:
    op.drop_column("ml_predictions", "current_tier")
