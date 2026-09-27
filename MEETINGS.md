# Meetings

How the communication module's video conferencing works, what it deliberately
does not do, and what has to be configured.

## What was there before

About ninety lines inside `AppMain.js`. It asked for a room name, prefixed it
with `AstroBSM_`, and mounted the **public** `meet.jit.si` in an iframe.

Two consequences, both live in production until 27 September 2026:

* **Meetings were effectively public.** Anyone on the internet who guessed
  `AstroBSM_weekly-standup` was in the management meeting. No password, no
  lobby, no token.
* **Nothing was recorded.** No meeting table existed. No schedule, no
  attendance, no invitation, no roles, no history.

A search of the repository for `WebRTC`, `RTCPeerConnection`, `WebSocket`,
`socket.io`, `STUN`, `TURN` and `iceServers` returned nothing. There was no
signalling infrastructure of our own to build on, and none has been added.

## Where the media runs, and why not here

The droplet has **1 vCPU and 961 MB of RAM**, shared with Postgres and the rest
of the ERP. An SFU — Jitsi's videobridge, LiveKit, mediasoup — needs several
cores and headroom this machine does not have. A browser-to-browser mesh is the
alternative, and it degrades badly past about four participants and still needs
a TURN server for users behind restrictive NATs.

So the audio, video, screen share, participant grid, in-meeting chat, device
pickers and network adaptation are **Jitsi's**, and never touch this server.
What this server keeps is everything Jitsi cannot know: who may join, who is
host, who came, and for how long.

## Two security postures

`meetings.jaas_configured()` is false until JaaS credentials are present.

| | Without JaaS (today) | With JaaS |
|---|---|---|
| Media server | public `meet.jit.si` | `8x8.vc`, your tenant |
| What keeps strangers out | the room name is 22 characters of `secrets.token_urlsafe`, server-issued, never derived from the title, and never sent to a browser the server has not admitted | the above, **plus** a JWT this server signs that the media server verifies |
| Host powers inside the call | Jitsi's own first-moderator behaviour | `moderator` claim, enforced by the media server |
| A forwarded room name | works — anyone admitted can pass it on, and it cannot be revoked from them | worthless without a signature |

The fallback is not an untested lesser mode: every meeting works either way, and
both the API (`/api/meetings/config`) and the screens report which posture is in
force. A host sending a link to a supplier should know whether it is protected
by a signature or by obscurity.

**Switching JaaS on** means setting `JAAS_APP_ID`, `JAAS_API_KEY` and
`JAAS_PRIVATE_KEY` and restarting. All three are required — a half-configured
tenant would mint signatures the media server rejects, which fails at the worst
moment, as somebody tries to join a meeting that has already started.

## Guest access

`https://erp.bonnesantemedicals.com/meet/<token>` renders before the
authentication gate, the same way `/order/<token>` and `/register/<token>` do.

The guest enters a name, chooses whether to start with microphone and camera on,
and either waits for the host or joins. No account, no download.

**A guest pass is not a session.** It is a JWT signed with
`MEETING_GUEST_SECRET` — deliberately *not* `SECRET_KEY` — and carries
`typ: "meeting_guest"`. `require_authenticated_user` refuses that claim
explicitly. The separate key alone would be enough; the claim means a deployment
that ever set both secrets to the same value still refuses. One of those is a
configuration mistake waiting to happen; two is a mistake that has to be made
twice. Both halves are tested.

The pass names one meeting and carries no role, no user id and no privileges.
There is no route in the application that accepts it for anything else.

**What a guest is told:** the meeting title, the host's name, the start time and
the duration. Not the agenda — an internal agenda routinely names customers,
figures and staff, and an invitation gets forwarded.

## Roles

| Role | Can |
|---|---|
| Host | everything below, plus end for everyone, cancel, lock, admit/reject, remove, regenerate and revoke the link |
| Co-host | the same meeting controls as the host |
| Internal participant | join, leave, mic/camera, screen share, chat |
| External guest | join, leave, mic/camera; screen share and chat only if the host allowed them |

Authorisation is **per meeting, not per ERP role**. An administrator is not
entitled to read what the sales team discussed with a supplier merely for being
an administrator; `meeting_detail` returns 404 to anybody who was not in it.

## The link

Issued when the meeting is created, and **shown once** — only its SHA-256 is
stored, exactly as the distributor ordering link works. A host who loses it
regenerates, which invalidates the old one immediately.

Regenerating does **not** disconnect people already in the meeting: they hold a
pass that was checked when they joined, and cutting a supplier off mid-sentence
because the host tidied up the invitations is a worse outcome than the risk it
removes. **Revoking** is the control for that, and it demands a reason.

Optional extras: a passcode (hashed with the application's own password
hashing), an expiry (default 30 days), a participant cap, and a lock.

## Attendance

Opened when somebody joins, closed when they leave. `left_at` is nullable on
purpose — a browser that crashes never tells us it left — and a row with no
`left_at` is reported as an unfinished observation rather than given an invented
departure time. Ending the meeting closes every row still open, which is the
moment we can honestly say everybody stopped attending.

This is the same discipline `call_logs.duration_source` applies to call length.

## The trail

`meeting_audit_logs` records creation, updates, link regeneration and
revocation, join requests, admissions, rejections, removals, locks and status
changes. It is **append-only, enforced by a database trigger** — it is what
somebody reads to find out who let an outsider into a meeting, and a convention
that a log is not edited is worth nothing next to a trigger that refuses.

A side effect worth knowing: **a meeting with audit rows cannot be deleted.**
The foreign key would null the reference, which is an UPDATE, which the trigger
refuses. That is the intended outcome — a meeting that happened is a record —
but it means cleanup is done by cancelling, not deleting.

## Configuration

| Variable | Required | Notes |
|---|---|---|
| `MEETING_GUEST_SECRET` | for guest links | Signs guest passes. **Must differ from `SECRET_KEY`.** Without it, staff meetings still work and guest links return 503. |
| `PUBLIC_BASE_URL` | recommended | Used to build join links. Defaults to the production domain. |
| `JAAS_APP_ID` | for signed links | From the 8x8 JaaS console. |
| `JAAS_API_KEY` | for signed links | The key id; becomes the JWT `kid` header. |
| `JAAS_PRIVATE_KEY` | for signed links | RSA private key, PEM. Never in git. |
| `JITSI_DOMAIN` | optional | Overrides the media server. |
| `MEETING_GUEST_TOKEN_MINUTES` | optional | Guest pass lifetime, default 240. |

All are passed through `docker-compose.yml` from `.env` on the server, which is
`chmod 600` and not in version control.

**Server requirements:** HTTPS (browsers refuse camera and microphone without
it) — already in place via Certbot. No new ports, no WebSocket of our own, no
TURN server to run: Jitsi provides all media infrastructure. nginx needs no
change.

## What was deliberately not built

* **Recording.** Consent, storage, retention, access control and download
  restriction are not afterthoughts, and the call-recording module already shows
  what doing it properly costs. The JaaS token explicitly sets
  `recording: false` rather than leaving it to a default. The architecture
  supports adding it later.
* **Our own WebRTC stack or SFU.** See the hardware constraint above.
* **A separate notification system.** Meeting events should be wired into the
  existing Web Push infrastructure; that is the next piece of work, not a second
  notifier.

## Tests

`backend/tests/test_meetings.py` — 32 tests. The ones that matter are not the
happy path:

* the room name owes nothing to the title, and two meetings never share one;
* the room never reaches a browser still in the waiting room;
* a guest pass is refused as a user session — and is *still* refused when the
  two secrets are made identical;
* revoked, expired, cancelled and ended links all refuse, and a guessed token
  says nothing about what exists;
* a meeting is invisible to somebody who was not in it;
* the audit trail cannot be rewritten or deleted;
* the landing page and the invitation carry no agenda and no room name.
