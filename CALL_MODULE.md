# Company Call Log & Call Tracking

**Bonnesante Medicals — ASTRO-ASIX ERP**

Staff pick a customer, tap **Call**, and the call is recorded against that
customer with a purpose and an outcome. Management sees who called whom, when,
how long for, and — crucially — **how much that duration can be trusted**.

---

## 1. The one thing to understand about durations

There are two completely different kinds of number in this module, and the
difference matters more than anything else here.

### Estimates (default)

When a staff member calls straight from their phone or on WhatsApp, the app
hands off to the dialer and is **backgrounded**. It learns nothing about the
call — no browser on Android or iOS exposes call state, and none is going to.

So the app times **how long it was in the background** and, when the staff
member returns, shows them that figure and asks them to confirm or correct it.

That number is **not the call**. It contains:

| Included | Typical size |
|---|---|
| Dialling and connecting | 2–5 seconds |
| Ringing before answer | 5–30 seconds |
| **The call itself** | the part you want |
| Delay before switching back to the app | 0 seconds to forever |

It is also **inflatable**: a staff member who wants a longer number only has to
wait before returning to the app. This is not a flaw that can be fixed in a web
app; it is what a web app is able to know.

### Verified durations (click-to-call bridging)

When bridging is switched on, the staff member taps Call and:

1. The telephony provider rings **their own phone**
2. They answer
3. The provider rings **the customer** and joins the two legs
4. When it ends, **the carrier reports the duration and the cost**

That figure comes from the network. Nobody in the company — not the caller, not
an administrator, not this application — can alter it. The database enforces
this: a duration may only be marked `VERIFIED` if a real provider reference is
attached, and **an estimate can never overwrite a verified figure**. Both rules
have tests.

### How every screen labels them

| Label | Meaning | Trust |
|---|---|---|
| **Network verified** | The carrier timed it | Hard fact |
| **Confirmed** | App timed it, staff member accepted the figure | Reasonable |
| **Timed by app** | App timed it, nobody confirmed | Soft |
| **Typed in** | Staff member entered it by hand | Self-reported |
| **Not known** | The app never saw them come back | None |

**No duration is ever displayed without its label.** The management overview
shows what proportion of reported minutes were hand-typed, and warns when that
proportion is high — because a total built mostly from typed figures is an
account of activity, not a measurement of it.

---

## 2. What was built

**Backend**

| File | Purpose |
|---|---|
| `alembic/versions/u0123456789t_call_log.py` | `call_logs` + immutability trigger |
| `alembic/versions/v1234567890u_call_telephony.py` | bridging columns, `call_provider_events` |
| `app/api/calls.py` | the call log API |
| `app/api/telephony_webhook.py` | **unauthenticated** provider callbacks |
| `app/services/telephony.py` | provider abstraction (Africa's Talking) |

**Frontend**

| File | Purpose |
|---|---|
| `src/CallModule.js` | *Make a Call* — contact picker, call flow, my log |
| `src/CallAdmin.js` | *Call Tracking* — management overview and full log |

**Tests**: `tests/test_calls.py` (9) and `tests/test_telephony.py` (15). All 24
pass against real PostgreSQL 15.

---

## 3. Where contacts come from

1. **Company customer database** — searchable server-side by name, code or
   number. Only customers who actually have a phone number are offered; a name
   with no number is just a dead tap.
2. **The phone's own address book** — via the Contact Picker API. This exists
   on **Chrome for Android only**, so the button appears only where it works.
3. **Typed manually** — for anyone in neither list.

Numbers are normalised, so `0803 123 4567`, `+2348031234567` and
`234-803-123-4567` are recognised as one number. Without that, a customer's
call history silently splits three ways.

---

## 4. What cannot be done, honestly

| Wanted | Web app | Native Android | Bridging |
|---|---|---|---|
| Phone call duration | Estimate | Exact | **Exact, verified** |
| **WhatsApp call duration** | ✗ | ✗ | n/a |
| Works on iPhone | ✅ | ✗ | ✅ |
| Needs an app installed | No | Yes | No |

**WhatsApp call durations are not obtainable by anyone.** WhatsApp calls are
VoIP inside WhatsApp; they do **not** appear in Android's call log, and
WhatsApp's Business Platform has no voice API at all. The only technique that
exists is scraping WhatsApp's ongoing-call notification, which breaks on every
WhatsApp update and reads notification content generally. It is not a
foundation for a business control.

A native Android app *could* read the ordinary call log via `READ_CALL_LOG` —
but Google Play restricts that permission to dialer, SMS and caller-ID apps, so
an ERP would be rejected. It would require sideloading or MDM onto company
phones, a separate Kotlin codebase, and would still not solve WhatsApp.

**This is why bridging is the recommended route:** it is the only option that
gives exact, unfalsifiable durations on every phone including iPhones, with no
app to install.

---

## 5. Turning bridging on

It is **off by default** and refuses to half-work — a partial configuration is
reported as unavailable rather than failing at the moment someone taps Call.

### Step 1 — Get a provider account

**Africa's Talking** is the natural choice here (Nigerian, naira billing, local
support). Twilio and Termii also work. You need a **voice-enabled number** —
that is the number your customers will see.

### Step 2 — Generate a webhook secret

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

This secret goes in the callback URL, which makes that URL **a password**. Give
it to the provider and nobody else, keep it out of logs, and rotate it if it
leaks.

### Step 3 — Set the environment variables

| Variable | Value |
|---|---|
| `TELEPHONY_PROVIDER` | `africastalking` |
| `AT_USERNAME` | your provider username |
| `AT_API_KEY` | your provider API key |
| `AT_CALLER_ID` | your voice number, e.g. `+2341234567` |
| `TELEPHONY_WEBHOOK_SECRET` | the secret from step 2 (min 24 chars) |
| `PUBLIC_BASE_URL` | `https://erp.bonnesantemedicals.com` |

Restart the backend. Then open `/api/calls/config` as an administrator — it
returns `bridging_available` and, if not, **exactly what is missing**.

### Step 4 — Point the provider at the callback

In the provider's dashboard, set the voice callback URL to:

```
https://erp.bonnesantemedicals.com/api/telephony/<YOUR_SECRET>/voice
```

### Step 5 — Make one test call

Call your own second phone. It should ring your primary phone first, then the
second. Within a minute the log should show the call with **Network verified**
and a cost.

If it doesn't, check `/api/calls/provider/events?unmatched_only=true` — every
callback is stored raw, exactly as it arrived, precisely so this is diagnosable.

### Staff phone numbers

Bridging rings the staff member's own line, so **their user profile needs a
phone number**. Anyone without one gets a clear error telling them to add it,
and can type the line they are on for a single call.

---

## 6. Security of the callback endpoint

The webhook is **unauthenticated by necessity** — a carrier cannot present a
bearer token. It lives in its own file (`telephony_webhook.py`) so that fact is
impossible to miss, and it is registered in `main.py` alongside the other
deliberately-public routers with the reason stated.

Defences, in the order they matter:

1. **Constant-time secret comparison** — the endpoint cannot be probed one
   character at a time by measuring response times.
2. **A wrong secret returns a flat 404** — indistinguishable from a path that
   does not exist, so a prober learns nothing.
3. **Every request is recorded before it is interpreted**, with its source
   address, so a stream of failed attempts is visible rather than silent.
4. **Nothing a caller sends chooses a call.** The session id must already
   appear on a row this application created when a real member of staff
   pressed Call. An unrecognised session is stored and refused, never acted on.
5. **The customer number is filtered before it enters the dial XML**, so a
   crafted number cannot inject extra instructions. There is a test for this.
6. **Call recording is off** and not exposed. Recording a conversation raises
   consent obligations under the NDPA that a duration figure does not — and
   this module exists to measure length, not to listen.

`call_provider_events` is append-only, enforced by a trigger, for the same
reason the wallet ledger is: it is the record of what the network told us.

---

## 7. Cost

Bridging uses **two legs** per call — one to the staff member, one to the
customer — so it costs roughly twice a single outbound minute. Worth knowing
before you switch it on.

Against that: the duration and cost are exact, per-call cost is captured
against the customer and department, and it is often still cheaper than the
airtime staff currently spend, which today buys you no record at all.

---

## 8. For staff — the short version

**Making a call:** *Make a Call* → **Make a call** → find the customer → choose
how (Company line, Phone, or WhatsApp) → say what it is about → **Call**.

- **Company line** (where available): your phone rings, answer it, you are
  connected. Nothing to confirm afterwards.
- **Phone / WhatsApp**: your dialer or WhatsApp opens. **Come back to the app
  when the call ends** and it will ask how long it took.

**Why the app asks:** it genuinely cannot see your call log. The figure it
offers includes ringing time, so it is usually a little long — correct it if
you know better. The record notes whether you confirmed the app's figure or
typed your own, which is fair to you as well as to the company.

**If a call did not connect:** tap *The call didn't happen*. The attempt still
appears in the log, which is itself useful information.
