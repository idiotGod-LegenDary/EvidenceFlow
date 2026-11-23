import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dialogue_states",
        sa.Column(
            "session_id", sa.String(64), sa.ForeignKey("sessions.session_id"), primary_key=True
        ),
        sa.Column("slots_json", sa.Text(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_table("dialogue_states")
