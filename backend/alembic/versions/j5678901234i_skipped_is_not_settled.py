"""A skipped settlement is deferred, not finished.

Revision ID: j5678901234i
Revises: i4567890123h
Create Date: 2026-09-19

WHAT WENT WRONG
===============
When a payment arrives for products that have no destination account mapped,
`distribute_payment` records a SKIPPED settlement saying so. That is right: the
money arrived and the payment must stand whatever the configuration looks like.

What was wrong is that SKIPPED then counted as a LIVE settlement everywhere
else. The consequence, found on the production database: 179 payments -- every
payment the company has ever taken -- sat at SKIPPED, and

  * the retry job passed over them, because it only looks for FAILED;
  * the Exceptions list did not show them, because it hides payments that
    already have a live settlement;
  * calling distribute on one answered "Already settled; nothing further was
    distributed" and did nothing;
  * and this unique index made a second attempt impossible anyway.

So configuring the accounts would have fixed new payments and silently left the
entire back catalogue stranded, with no screen anywhere admitting it.

THE DISTINCTION THIS MIGRATION DRAWS
====================================
COMPLETED and PENDING are settlements. They own the payment and nothing else may
touch it -- that is what stops a destination account being paid twice.

SKIPPED is the absence of a settlement with a note attached. It has moved no
money, so it must not own the payment: it belongs in the exceptions list, it
must be retried once the configuration it was waiting for exists, and a later
successful attempt must be insertable beside it.

The SKIPPED rows are kept. They are the record of why that money sat where it
was, and when. This only changes what they BLOCK, never what they say.
"""
from alembic import op

revision = 'j5678901234i'
down_revision = 'i4567890123h'
branch_labels = None
depends_on = None


def upgrade():
    # Rebuilt, not created: the old index covered SKIPPED and would refuse the
    # very retry this change exists to allow. Dropped and recreated rather than
    # altered, because a partial index's WHERE clause cannot be changed in place.
    op.execute("DROP INDEX IF EXISTS uq_settlement_live_per_payment")
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_settlement_live_per_payment
            ON settlements (payment_id)
         WHERE status IN ('PENDING','COMPLETED')
    """)

    # The exceptions list and the retry job read this often enough, over a
    # table that only grows, to be worth an index.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_settlements_awaiting_config
            ON settlements (payment_id)
         WHERE status IN ('SKIPPED','FAILED')
    """)


def downgrade():
    # Going back narrows what is allowed, so a payment that has both a SKIPPED
    # row and a later COMPLETED one would violate the restored index. Those
    # rows are exactly what this migration set out to create, so the downgrade
    # retires the superseded SKIPPED rows rather than failing or deleting them.
    op.execute("""
        UPDATE settlements s
           SET status = 'REVERSED',
               failure_reason = COALESCE(failure_reason, '')
                   || ' [superseded; retired by downgrade of j5678901234i]'
         WHERE s.status = 'SKIPPED'
           AND EXISTS (SELECT 1 FROM settlements live
                        WHERE live.payment_id = s.payment_id
                          AND live.status IN ('PENDING','COMPLETED'))
    """)
    op.execute("DROP INDEX IF EXISTS ix_settlements_awaiting_config")
    op.execute("DROP INDEX IF EXISTS uq_settlement_live_per_payment")
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_settlement_live_per_payment
            ON settlements (payment_id)
         WHERE status IN ('PENDING','COMPLETED','SKIPPED')
    """)
