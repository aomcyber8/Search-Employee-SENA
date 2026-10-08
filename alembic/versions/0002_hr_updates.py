"""Add HR update snapshots and employment status."""

from alembic import op
import sqlalchemy as sa


revision = "0002_hr_updates"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "hr_update_runs" not in tables:
        op.create_table(
            "hr_update_runs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("source_url", sa.String(length=700), nullable=False),
            sa.Column("reported_count", sa.Integer()),
            sa.Column("parsed_count", sa.Integer()),
            sa.Column("working_count", sa.Integer()),
            sa.Column("resigned_count", sa.Integer()),
            sa.Column("duplicate_name_count", sa.Integer()),
            sa.Column("error_message", sa.Text()),
            sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True)),
            sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        )
        op.create_index("ix_hr_update_runs_status", "hr_update_runs", ["status"])
        op.create_index("ix_hr_update_runs_is_current", "hr_update_runs", ["is_current"])
        op.create_index("ix_hr_update_runs_created_by_id", "hr_update_runs", ["created_by_id"])
    if "hr_update_records" not in tables:
        op.create_table(
            "hr_update_records",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("run_id", sa.Integer(), sa.ForeignKey("hr_update_runs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("source_order", sa.Integer(), nullable=False),
            sa.Column("employee_code", sa.String(length=80)),
            sa.Column("full_name", sa.String(length=255), nullable=False),
            sa.Column("normalized_name", sa.String(length=255), nullable=False),
            sa.Column("department", sa.String(length=500)),
            sa.Column("position", sa.String(length=255)),
            sa.Column("email", sa.String(length=255)),
            sa.Column("mobile", sa.String(length=255)),
            sa.Column("office_phone", sa.String(length=255)),
            sa.UniqueConstraint("run_id", "source_order", name="uq_hr_run_source_order"),
        )
        for column in ("run_id", "employee_code", "full_name", "normalized_name", "department", "email"):
            op.create_index(f"ix_hr_update_records_{column}", "hr_update_records", [column])

    people_columns = {column["name"] for column in sa.inspect(bind).get_columns("people")}
    missing_people_columns = {
        "employment_status", "employment_status_updated_at", "hr_update_run_id"
    } - people_columns
    if missing_people_columns:
        with op.batch_alter_table("people") as batch_op:
            if "employment_status" in missing_people_columns:
                batch_op.add_column(sa.Column("employment_status", sa.String(length=30)))
            if "employment_status_updated_at" in missing_people_columns:
                batch_op.add_column(sa.Column("employment_status_updated_at", sa.DateTime(timezone=True)))
            if "hr_update_run_id" in missing_people_columns:
                batch_op.add_column(sa.Column("hr_update_run_id", sa.Integer()))
                batch_op.create_foreign_key(
                    "fk_people_hr_update_run_id", "hr_update_runs", ["hr_update_run_id"], ["id"]
                )
        if "employment_status" in missing_people_columns:
            op.create_index("ix_people_employment_status", "people", ["employment_status"])
        if "hr_update_run_id" in missing_people_columns:
            op.create_index("ix_people_hr_update_run_id", "people", ["hr_update_run_id"])


def downgrade():
    op.drop_index("ix_people_hr_update_run_id", table_name="people")
    op.drop_index("ix_people_employment_status", table_name="people")
    with op.batch_alter_table("people") as batch_op:
        batch_op.drop_column("hr_update_run_id")
        batch_op.drop_column("employment_status_updated_at")
        batch_op.drop_column("employment_status")
    op.drop_table("hr_update_records")
    op.drop_table("hr_update_runs")
