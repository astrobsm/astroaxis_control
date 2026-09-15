"""Keep a registration link readable, because it is meant to be published.

WHY THIS REVERSES A DECISION MADE ONE MIGRATION AGO
==================================================
`h3456789012g` stored only a SHA-256 of the registration token, copied from the
ordering link in `a6789012345z`. That is right for an ordering link and wrong
for this one, and the difference is what the link IS.

An ordering link is a credential. It places orders on one named distributor's
account, it is sent to one person, and it must never be forwarded. Storing only
a fingerprint means a stolen database does not yield a working link, and the
cost -- it cannot be shown twice -- is a cost worth paying.

A registration link grants nothing. It lets the holder fill in a form that lands
in a review queue. It is meant to be printed on a flyer, put on a banner at a
trade fair and forwarded through WhatsApp groups. A secret printed on a flyer is
not a secret, so hashing it bought no security at all -- and it cost the one
thing the feature is for: the first link issued in production was shown once,
the page was reloaded, and it could never be recovered or shared.

So the token is now kept in plain text for registration links only. Ordering
links are untouched and stay hash-only.

WHAT DOES NOT CHANGE
====================
Lookup still goes through `token_sha256`, which stays NOT NULL and stays the
unique index. The plain token is stored beside it for DISPLAY, so that staff can
copy the link again; it is never the thing a request is matched against. That
keeps one way to resolve a token and avoids two columns that could disagree.

LINKS ISSUED BEFORE THIS MIGRATION CANNOT BE RECOVERED
======================================================
Their `token` is NULL and there is nothing to derive it from -- that is what a
hash is. The column is nullable for exactly that reason, and the screen says so
against those rows rather than showing an empty box. They have to be revoked and
reissued. There is no data loss here beyond what was already lost.
"""
from alembic import op

revision = 'i4567890123h'
down_revision = 'h3456789012g'
branch_labels = None
depends_on = None


def upgrade():
    # IF NOT EXISTS, like every other statement in this chain: the migrations
    # are applied both by alembic and, in the tests, on top of a schema alembic
    # has already built. A migration that can only run once cannot be tested.
    op.execute("""
        ALTER TABLE distributor_registration_links
          ADD COLUMN IF NOT EXISTS token TEXT
    """)
    op.execute("""
        COMMENT ON COLUMN distributor_registration_links.token IS
          'The link in plain text, so it can be copied again. Safe here and '
          'only here: a registration link is meant to be published and grants '
          'nothing. Lookup still goes through token_sha256.'
    """)


def downgrade():
    op.execute("""
        ALTER TABLE distributor_registration_links DROP COLUMN IF EXISTS token
    """)
