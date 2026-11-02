"""Invalidate existing sessions atomically when an established password changes.

Revision ID: 0022_password_session_revocation
Revises: 0021_kb_hybrid_search
Create Date: 2026-11-01

Better Auth 1.7.7 updates the credential hash before its separate session-revocation
statement. If that second statement fails, every old token otherwise survives with the new
password committed. This trigger deletes the user's sessions in the same PostgreSQL
transaction as a real credential password update.

The first password is intentionally excluded: Better Auth inserts a credential row (or updates
a NULL password) and then preserves the caller while revoking the other sessions. A database
trigger cannot identify that caller. OAuth account writes are excluded too. On the regular
change-password path Better Auth creates the replacement session after the password update, so
that new session is not deleted.

Additive and backward compatible: old application code can run on this schema. The function is
invoker-security (the PostgreSQL default); the auth role already has DELETE on auth_sessions.
"""

from alembic import op

revision = "0022_password_session_revocation"
down_revision = "0021_kb_hybrid_search"
branch_labels = None
depends_on = None

FUNCTION = "auth_accounts_revoke_sessions_on_password_change"
TRIGGER = "auth_accounts_revoke_sessions_on_password_change"
CREATE_FUNCTION_SQL = """
CREATE FUNCTION auth_accounts_revoke_sessions_on_password_change() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
BEGIN
    DELETE FROM public.auth_sessions WHERE user_id = NEW.user_id;
    RETURN NEW;
END $$
"""
CREATE_TRIGGER_SQL = """
CREATE TRIGGER auth_accounts_revoke_sessions_on_password_change
AFTER UPDATE OF password ON auth_accounts
FOR EACH ROW
WHEN (
    OLD.provider_id = 'credential'
    AND NEW.provider_id = 'credential'
    AND OLD.password IS NOT NULL
    AND NEW.password IS NOT NULL
    AND OLD.password IS DISTINCT FROM NEW.password
)
EXECUTE FUNCTION auth_accounts_revoke_sessions_on_password_change()
"""


def upgrade() -> None:
    op.execute(CREATE_FUNCTION_SQL)
    op.execute(CREATE_TRIGGER_SQL)


def downgrade() -> None:
    op.execute(f"DROP TRIGGER {TRIGGER} ON auth_accounts")
    op.execute(f"DROP FUNCTION {FUNCTION}()")
