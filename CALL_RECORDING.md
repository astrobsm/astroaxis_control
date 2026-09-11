# Call Recording — setup, obligations, and the wording you need

**Bonnesante Medicals — ASTRO-ASIX ERP**

> **Read this before switching recording on.** The technical setup takes ten
> minutes. The obligations it creates are permanent, and two of the required
> steps are things only you can do — not the software.

---

## 1. What you are taking on

A call recording is personal data about **two people**: your employee and your
customer. Neither of them is a party to your database. Under the **Nigeria Data
Protection Act 2023** that means:

| Obligation | Who does it | Status |
|---|---|---|
| A lawful basis for recording | **You** — decide and document it | ⬜ Your action |
| Tell staff in writing, before recording starts | **You** — contract or policy | ⬜ Your action |
| Tell the customer before recording | **Staff, on every call** | ⚠️ See §4 |
| Keep recordings only as long as needed | The system, automatically | ✅ Built |
| Destroy them when that period ends | The system, automatically | ✅ Built |
| Restrict who can listen | The system | ✅ Built |
| Be able to say who listened | The system | ✅ Built |
| Honour an erasure request | The system, on your instruction | ✅ Built |

**The system cannot do the first two for you.** If you switch recording on
without them, you are recording customers unlawfully, and the fact that the
software made it easy will not help.

---

## 2. The one limitation you must design around

The automated announcement plays to **your staff member, not the customer.**

This is not a bug that can be fixed. The provider rings the staff member first;
they answer; only *then* is the customer dialled. Anything spoken before the
bridge is heard by whoever is already on the line — which is the employee.

So the announcement is a **per-call reminder to the employee**, and the
customer notice depends on the staff member actually saying it. The system
does what it can to make that reliable:

- The exact wording is shown on screen **before** the call is placed
- The same wording appears in this document and in staff policy
- `announced` is recorded per call, so you can see the notice was configured
- The Recordings screen shows **"Notice: Not given"** in red where it was not

If you later move to a provider that supports a *whisper* to the called party,
set `RECORDING_CALLEE_ANNOUNCE_URL` and the customer will hear it directly.
Africa's Talking has no such hook today.

---

## 3. Setup

### Step 1 — Create a Space

DigitalOcean → **Spaces Object Storage** → Create. Pick a region near your
droplet (`nyc3` if your droplet is in NYC). **Keep it private** — do not enable
public file listing. Then create a **Spaces access key** and note both halves;
the secret is shown once.

### Step 2 — Set the environment variables

| Variable | Example | Notes |
|---|---|---|
| `CALL_RECORDING_ENABLED` | `true` | Separate from bridging, deliberately |
| `CALL_RECORDING_RETENTION_DAYS` | `90` | After this, recordings are destroyed |
| `SPACES_KEY` | `DO00...` | Spaces access key |
| `SPACES_SECRET` | `...` | Spaces secret |
| `SPACES_BUCKET` | `bonnesante-recordings` | Your Space name |
| `SPACES_REGION` | `nyc3` | Must match the Space |
| `SPACES_ENDPOINT` | `https://nyc3.digitaloceanspaces.com` | Regional, no bucket |

Bridging must already be working — recording only applies to bridged calls.

Restart the backend, then check as an administrator:

```
GET /api/calls/recording/config
```

It returns `recording_enabled`, and if not, **exactly what is missing**.

### Step 3 — Set a lifecycle rule on the Space (belt and braces)

In the Space settings, add a lifecycle rule deleting objects under
`call-recordings/` after your retention period **plus a few days**.

The application sweep is the primary mechanism. This is the backstop for the
day the sweep silently stops running — which is the failure mode that turns a
retention policy into a fiction.

### Step 4 — Put the staff notice in writing

**This is the step people skip.** Add to contracts or the staff handbook:

> **Recording of business calls.** Calls placed through the company telephone
> system are recorded for quality assurance, training and record-keeping. You
> must inform the customer at the start of every call that it is being
> recorded. Recordings are retained for [90] days and then destroyed. Access is
> restricted to authorised administrators and every access is logged. If you
> have questions about this, contact [name / role].

Have staff acknowledge it. Keep the acknowledgements.

### Step 5 — Brief staff on the opening line

The default, shown on their screen before every call:

> *"Good day, this is [name] from Bonnesante Medicals. Please note this call is
> being recorded for our records."*

Change it with `CALL_RECORDING_STAFF_SCRIPT` if you prefer different wording —
but keep it to one sentence, at the start, in plain language.

### Step 6 — Test

Call your own second phone. You should hear the announcement, the call should
connect, and within a minute the recording should appear under **Call Tracking
→ Recordings** as `STORED` with a destruction date. Play it back, then check
**"Who has heard this?"** — your own playback should already be listed.

---

## 4. Running it

**Daily/weekly:** nothing. The system stores and expires recordings on its own.

**Monthly:** open **Call Tracking → Recordings** and check **Past retention**
reads zero. If it does not, press **Run retention sweep**. If it *still* does
not drop to zero, something is failing to delete — that is a real problem and
needs attention, not another sweep.

**If a customer asks to be erased:** find the call, press **Destroy**, give the
reason. The audio goes for good; a row remains showing it existed and was
destroyed on your instruction, which is what proves you complied.

**If a customer asks who has heard their call:** open the recording,
**"Who has heard this?"**. That log cannot be edited by anyone, including
administrators — which is exactly why it is worth something as an answer.

---

## 5. What is enforced, and how

These are not conventions. The database refuses the alternatives:

| Rule | Enforcement |
|---|---|
| Every recording has a destruction date | `retention_until` is `NOT NULL` |
| Retention can be shortened, never extended | Trigger rejects an increase |
| A stored recording must say where it is | `ck_rec_stored_has_key` |
| A deleted one must keep no pointer to the audio | `ck_rec_deleted_has_no_key` |
| A destroyed recording cannot come back | Trigger rejects `DELETED → STORED` |
| The row survives, to prove destruction happened | Trigger rejects `DELETE` |
| The access log cannot be altered by anyone | Trigger rejects `UPDATE` and `DELETE` |
| Playback is logged before the link is issued | Same transaction |
| Refused attempts are logged too | `DENIED` rows |
| Playback links expire | 5-minute presigned URLs |

Twenty tests cover these, including the AWS signing vector that storage depends
on. `pytest tests/test_recording.py`.

---

## 6. Cost

- **Storage**: DigitalOcean Spaces starts around $5/month for 250 GB. Audio is
  roughly 1 MB per minute, so 1,000 calls of 3 minutes is about 3 GB — well
  inside the base tier.
- **Bandwidth**: playback is streamed direct from Spaces to the browser, never
  through your droplet. That is deliberate: the droplet has under a gigabyte of
  RAM.
- **Recording itself**: some providers charge a small per-minute premium on top
  of the call. Check before you enable it.

---

## 7. Turning it off

Set `CALL_RECORDING_ENABLED=false` and restart. New calls stop being recorded
immediately.

Recordings already held are **not** deleted by this — their retention dates
still apply and the sweep still honours them. To destroy everything now, delete
each from the Recordings screen, or shorten retention and run the sweep.
