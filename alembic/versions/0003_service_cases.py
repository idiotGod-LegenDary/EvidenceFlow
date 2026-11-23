import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "service_cases",
        sa.Column("case_id", sa.String(32), primary_key=True),
        sa.Column("action_id", sa.String(64), nullable=False),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("title", sa.String(96), nullable=False),
        sa.Column("request_text", sa.Text(), nullable=False),
        sa.Column("order_id", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("action_json", sa.Text(), nullable=False),
        sa.Column("knowledge_version", sa.String(64), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("order_before_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("verification_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("notice", sa.Text(), nullable=False, server_default=""),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("action_id", "session_id", "user_id", "order_id", "status"):
        op.create_index(
            f"ix_service_cases_{column}", "service_cases", [column], unique=column == "action_id"
        )
    op.create_table(
        "case_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("case_id", sa.String(32), sa.ForeignKey("service_cases.case_id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("trace_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_case_events_case_id", "case_events", ["case_id"])


def downgrade() -> None:
    op.drop_table("case_events")
    op.drop_table("service_cases")
