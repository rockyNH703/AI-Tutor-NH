# Fixtures for .semgrep/netsec.yml (semgrep --test). Never imported or run.
import logging

import sqlalchemy as sa
from sqlalchemy import text

log = logging.getLogger(__name__)


def sql(session, table, user_id, limit):
    # ruleid: netsec-sql-text-built-string
    session.execute(text(f"SELECT * FROM {table}"))
    # ruleid: netsec-sql-text-built-string
    session.execute(sa.text("SELECT * FROM " + table))
    # ruleid: netsec-sql-text-built-string
    session.execute(text("SELECT * FROM t LIMIT %d" % limit))
    # ruleid: netsec-sql-text-built-string
    session.execute(text("SELECT * FROM {}".format(table)))
    # ok: netsec-sql-text-built-string
    session.execute(text("SELECT * FROM users WHERE id = :id"), {"id": user_id})
    # ok: netsec-sql-text-built-string
    session.execute(sa.text("SELECT 1"))


def logs(content, body, password, access_token, message, rows):
    # ruleid: netsec-log-sensitive-value
    log.info("reply saved", extra={"text": content})
    # ruleid: netsec-log-sensitive-value
    log.warning("login failed for %s", password)
    # ruleid: netsec-log-sensitive-value
    log.error(f"provider refused {access_token}")
    # ruleid: netsec-log-sensitive-value
    log.info("message", extra={"m": message.content})
    # ruleid: netsec-log-sensitive-value
    log.exception("bad request", extra={"payload": body})
    # ok: netsec-log-sensitive-value
    log.info("reply saved", extra={"chars": len(content)})
    # ok: netsec-log-sensitive-value
    log.info("auth code queued", extra={"code_type": body.type})
    # ok: netsec-log-sensitive-value
    log.info("notifications read", extra={"rows": rows})
