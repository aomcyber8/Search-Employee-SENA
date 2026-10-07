"""Initial employee directory schema."""
from alembic import op
from app.db import Base
from app import models  # noqa: F401

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    Base.metadata.create_all(bind=bind)
    if bind.dialect.name == "postgresql":
        op.execute("CREATE INDEX IF NOT EXISTS ix_people_full_name_trgm ON people USING gin (full_name gin_trgm_ops)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_org_units_name_trgm ON organization_units USING gin (name gin_trgm_ops)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_assignments_position_trgm ON assignments USING gin (position gin_trgm_ops)")


def downgrade():
    Base.metadata.drop_all(bind=op.get_bind())
