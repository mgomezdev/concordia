# Tips & FAQ

---

## Tips

**Set `THEMIS_BOOTSTRAP_KEY` before your first start.** It costs nothing and it's the
difference between "annoying" and "wipe everything and start over" if the
[key trap](../setup/03-first-login.md#the-first-key-trap) catches you.

**Use the Mock Printer before hardware arrives.** It exercises the queue, slicing, and the
whole job lifecycle with no printer attached. Every Themis screenshot in this wiki was made
with one.

**Keep loaded filament accurate.** It's the single most common cause of blocked jobs. Themis
matches type *and* colour, and it can only work from what you've told it.

**Make jobs eligible for more printers than you think.** Eligibility is permission, not
assignment. More eligible printers = more scheduling freedom and fewer idle machines.

**Export a fleet backup once you're set up.** Settings → Fleet Backup. Printer configs are
tedious to rebuild.

**Prefer thin `inherits` presets over flattened ones.** Flattened profiles are frozen at the
OrcaSlicer version they came from and silently miss later vendor fixes.
See [Profiles & Slicing](../using/profiles-and-slicing.md#adding-your-own-presets).

**Navigate Settings by clicking, not by URL.** The routes aren't all guessable and an unknown
one silently renders Tags.

**Don't publish Laminus.** It has no authentication at all. If you expose it to load profiles,
remove the port mapping afterwards.

**`docker compose down -v` also nukes the OrcaSlicer cache.** You'll pay the 5–10 minute cold
start again. Use plain `down` unless you actually want a factory reset.

---

## FAQ

### Do I need OrcaSlicer installed?
No. Laminus downloads and runs its own copy. You only need OrcaSlicer on your desktop if you
want to *author* presets or models.

### Do I need to clone Themis, Laminus, and Ordinus?
No. `docker compose up` pulls published images. You only need sibling checkouts for the
local-build overlay.

### Why is the first start so slow?
Laminus downloads a ~300 MB OrcaSlicer AppImage and then scans 11,000+ vendor profiles. Cold:
5–10 minutes. Warm: ~20–30 seconds, because the AppImage is cached in a named volume.

### Why can't I open Laminus in a browser?
It has no published port, by design — it's an internal service with no authentication. See
[Reaching Laminus directly](../using/profiles-and-slicing.md#reaching-laminus-directly).

### Why did my job go BLOCKED?
It can't run *right now* — almost always a filament mismatch. It stays queued and retries.
Load the right spool. See [Blocked vs failed](../using/first-print.md#blocked-vs-failed).

### A printer is idle but jobs are queued. Why?
Likely one of: the printer is waiting on the plate-clear hold; queue-on is off; it has no
machine profile; or the **first** eligible job can't run on it and head-of-line behaviour
makes it wait rather than skip ahead. Look at the first job in the queue.

### Why does slicing happen "on claim" instead of at upload?
Because the slice is machine-specific. If three printers are eligible, slicing up front would
mean computing three results and discarding two.

### Can I run this on a Raspberry Pi?
Not usefully. OrcaSlicer is x86-64 and slicing is CPU-heavy. Run the stack on a normal
machine; printers connect over the network.

### Can I access it from my phone?
Yes, on the same network — use the host machine's LAN IP instead of `localhost`
(`http://192.168.1.50:8001`). Ordinus has a genuine mobile layout. **Don't forward these ports
through your router.**

### How do I update?
```bash
docker compose pull
docker compose up -d
```
Data in volumes is preserved. Note this pulls `:latest`, so it may not match this wiki.

### How do I back up?
Back up `themis-data`, `laminus-config`, `ordinus-config`, and `ordinus-data`. Skip
`laminus-slicer` — it's a rebuildable cache. The prod overlay puts all of it in host
directories, which is much easier to back up.
See [Configuration](configuration.md#the-prod-overlay).

### Is it safe on my network?
On a trusted home LAN behind a router, broadly yes. **Not** on a shared or hostile network,
and never exposed to the internet: Themis serves everything unauthenticated until a key
exists, and Laminus has no auth at all.

---

## Where the READMEs are wrong

Verified against a stack running the published `:latest` images on **2026-09-20**. Where the
apps' own READMEs disagree with the running software, this wiki follows the software.

| Claim | Reality |
|---|---|
| Themis README: notifications via "built-in **ntfy, Discord, and email** channels" | **Not present.** No notifications route exists in the running API. The only mechanism is the generic signed webhook at `/api/v1/settings/webhook`. |
| Themis README: an **Orders** screen in the app | No Orders item in the sidebar. Orders are reached via Projects. The `/api/v1/orders` API is fully present — a navigation gap, not missing functionality. |
| Themis Fleet empty state: "Connect a Bambu, Elegoo, Snapmaker, **Prusa or OctoPrint** host" | Only four types are offered: Bambu Lab, Elegoo Centauri, Snapmaker U1 (Extended), Mock Printer. No Prusa, no OctoPrint. |
| Themis README Docker section: a two-service compose bind-mounting `%APPDATA%\OrcaSlicer` | Describes the pre-Concordia setup. The current stack is three services and does not bind-mount your host OrcaSlicer config. |
| Concordia `CLAUDE.md`: "two sibling repos" | There are **three** — it omits Ordinus entirely, which `docker-compose.yml` and `CONTRIBUTING.md` both include. |
| `.env.example` `ORDINUS_PORT=3002` | `docker-compose.yml`'s internal fallback is `3001`. They disagree; with a `.env` present you get 3002. |

### Known rough edges

Observed directly, minor but confusing:

- **Creating the first API key locks the browser out** and never displays the key's plaintext.
  The bootstrap key is then rejected by the prompt that asks for a key, and after a reload the
  prompt mislabels the state as "Couldn't reach the Themis server".
  Full detail: [the first-key trap](../setup/03-first-login.md#the-first-key-trap).
- **Unknown `/settings/...` paths silently render Tags** instead of a not-found. The webhook
  route is singular (`/settings/webhook`).
- **Fleet header reads `1 printers`**, and the sidebar printer count can lag until reload.
- **Laminus health reports `orcaslicer_version: "Invalid option --version"`.** Cosmetic — the
  build doesn't support that flag. `orcaslicer_installed: true` is the real signal.
- **Some profile "makes" are name-parsing artifacts** — `Elegoo`, `Elegoo Centauri`, and
  `Elegoo Centauri Carbon` appear as separate makes, and some have fragment model lists like
  `2`.
- **Ordinus reports version `1.0.0`** at `/api/v1/health` while its `package.json` says
  `3.1.0`.
