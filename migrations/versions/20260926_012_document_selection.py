"""Enable explicit, purpose-scoped selection of uploaded contract materials.

Revision ID: 20260926_012
Revises: 20260926_011
"""

from alembic import op

revision = "20260926_012"
down_revision = "20260926_011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE contract_selections DROP CONSTRAINT contract_selections_relation_check")
    op.execute(
        "ALTER TABLE contract_selections ADD CONSTRAINT contract_selections_relation_check "
        "CHECK (relation IN ('main', 'attachment', 'background', 'upstream'))"
    )
    op.execute("GRANT INSERT ON contract_selections, document_grants TO contract_runtime")
    op.execute("GRANT UPDATE (status) ON document_versions TO contract_runtime")
    op.execute(
        """
        CREATE FUNCTION guard_document_status_transition() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.status = OLD.status
             OR (OLD.status = 'staged' AND NEW.status = 'active')
             OR (OLD.status = 'active' AND NEW.status IN ('superseded', 'revoked')) THEN
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'document_status_transition_invalid' USING ERRCODE = '23514';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER document_status_transition_guard BEFORE UPDATE OF status "
        "ON document_versions FOR EACH ROW EXECUTE FUNCTION guard_document_status_transition()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER document_status_transition_guard ON document_versions")
    op.execute("DROP FUNCTION guard_document_status_transition()")
    op.execute("REVOKE UPDATE (status) ON document_versions FROM contract_runtime")
    op.execute("REVOKE INSERT ON contract_selections, document_grants FROM contract_runtime")
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM contract_selections WHERE relation = 'attachment') THEN
            RAISE EXCEPTION 'cannot_downgrade_with_attachment_selections';
          END IF;
        END $$
        """
    )
    op.execute("ALTER TABLE contract_selections DROP CONSTRAINT contract_selections_relation_check")
    op.execute(
        "ALTER TABLE contract_selections ADD CONSTRAINT contract_selections_relation_check "
        "CHECK (relation IN ('main', 'background', 'upstream'))"
    )
