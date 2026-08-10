# archive/

One-shot scripts that have already done their job, kept for the record.

Nothing here is imported by the application or used by the current deploy
path. They were moved out of the repository root — where they outnumbered the
tooling that is still in use — with `git mv`, so every file keeps its full
history. Recovering one is a single `git mv` back.

## What is in here

**Mojibake and emoji cleanups** — `clean_all_emojis.py`, `clean_emojis.py`,
`clean_frontend_emojis.py`, `finish_emoji_cleanup.py`, `fix_frontend_emojis.py`,
`remove_emojis.py`, `fix-emojis*.ps1`, `remove-emojis*.ps1`. Several passes at
stripping garbled characters out of `frontend/src/AppMain.js`. The characters
are gone; the scripts carry hardcoded absolute paths and would not run
anywhere else.

**UI and form patchers** — `fix-ui-modules*.{py,ps1}`, `fix-form-accessibility.*`,
`enhance-sales-form.py`, `final-sales-form.py`, `sales-form-fix.py`,
`enhance-raw-materials.*`, `enhanced-raw-materials-module.js`,
`comprehensive_enhancement.py`, `comprehensive_ui_enhancement.py`,
`final-enhancement.py`, `final-fix.py`, `add-transfer-button.py`. Scripts that
rewrote source files in place. Their output is committed; re-running them
against today's code would corrupt it.

**Superseded deploy scripts** — `deploy-frontend*.ps1` (five variants),
`deploy-backend-bulk.ps1`, `deploy-bulk-upload.ps1`,
`deploy-full-production.ps1`, `deploy-now.ps1`. All replaced by `deploy.ps1`
in the repository root, which does the same job and carries fixes they lack:
`ssh -n` (without it the script hangs on Windows, indefinitely and silently),
a `docker-compose` recreate fallback for the v1 `ContainerConfig` bug, and
host-side health probing on `127.0.0.1` — a container healthcheck can pass
while nginx still returns 502.

**Ad-hoc test scripts** — `test-cache-fix.ps1`, `test-multi-unit-*.ps1`,
`test-pwa.ps1`, `test-simple.ps1`, `clear-cache-and-test.ps1`,
`test_auth_import.py`, `parse_staff.py`. Manual checks from specific
investigations, several of them empty files. The real suite is `backend/tests/`.

## Still in the repository root, and still used

`deploy.ps1`, `deploy-mapd.ps1`, `deploy-droplet.sh`, `deploy-new-droplet.ps1`,
`build-and-serve.ps1`, `create_admin.py`, `create_admin_user.py`,
`manage_warehouse_access.ps1`, `setup-duckdns.sh`, `setup-ssh-key.ps1`,
`test_api.sh`, `test_api_v2.sh`, `update_payment_modes.ps1`, `update_staff.sh`,
`cache-reset-utility.js`.
