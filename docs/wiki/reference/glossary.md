# Glossary

Plain-language definitions for terms used across this wiki and the apps.

---

### 3MF
A 3D model file format. Unlike STL it can carry **multiple plates**, per-object settings, and
colour/filament assignments. Prefer it when you have the choice. In Themis, **each plate of a
multi-plate 3MF becomes its own job**.

### AMS
Bambu Lab's *Automatic Material System* — the multi-spool changer. Themis reads its slots
automatically so it knows what filament is loaded where.

### Blocked
A job that **can't run right now** but is still queued and retried every cycle. Usually a
filament mismatch. **Not an error** — load the right spool and it proceeds by itself.
Contrast with *failed*. See [Blocked vs failed](../using/first-print.md#blocked-vs-failed).

### BOM (Bill of Materials)
The list of parts and quantities needed to build something. Ordinus generates one from a
drawer layout; Themis Projects turn one into jobs.

### Bootstrap key
`THEMIS_BOOTSTRAP_KEY` — a fixed API key Themis always accepts, independent of its key store.
The recovery path when the normal key system locks you out.
See [First Login & Security](../setup/03-first-login.md).

### Catalog
Laminus's index of every available OrcaSlicer profile (machine, process, filament). Built by
scanning at startup. Until it's loaded, nothing can slice — which is why Themis waits for
`catalog_loaded: true`.

### Claim
When the queue engine assigns a queued job to an idle, eligible printer. Slicing happens **on
claim**, so the job is sliced for the machine that will actually print it.

### Container / image
An **image** is a sealed package of an application plus everything it needs. A **container**
is a running copy of one. You don't install the apps — Docker fetches images and runs them.

### Failed
A job that hit a **terminal** error after slicing — the upload or start failed. Does not retry
itself. Contrast with *blocked*.

### Filament preset
OrcaSlicer settings for one material — temperatures, flow, cooling. One of the three presets
every slice needs.

### Gcode
The instruction list a printer actually executes: move here, extrude this much, heat to that.
The output of slicing.

### Gridfinity
An open-source modular storage system on a **42 mm grid**. Bins snap into baseplates. What
Ordinus designs layouts for.

### Healthcheck
A command Docker runs repeatedly to decide whether a service is genuinely working, not merely
started. Themis waits for Laminus's healthcheck to pass before starting.

### HMAC signature
A cryptographic fingerprint of a message computed with a shared secret. Themis signs webhook
requests with one so your receiver can verify they really came from Themis.

### Machine preset
OrcaSlicer settings for one printer — bed size, nozzle diameter, motion limits. Assigned per
printer in Themis; **required before that printer can slice**.

### Plate
One bed's worth of printing. A 3MF can contain several; each becomes a separate job.

### Plate-clear hold
After finishing, a printer is held until you confirm the plate is empty — so it can't print
onto the finished part. Released with **Ready for new work** on the Fleet card.

### Process preset
OrcaSlicer settings for *how* to print — layer height, speeds, infill, supports. Chosen per
job.

### Scope
A permission attached to an API key, per area and split Read / Write (plus **Control** for
printers). A read-only key can display status but change nothing.

### Sidecar
A helper service that runs alongside the main app and does one specialised job. **Laminus is
Themis's sidecar** — it exists solely to run OrcaSlicer.

### Slicing
Converting a 3D model into printer instructions (gcode) for a **specific** machine with
**specific** settings. See [Profiles & Slicing](../using/profiles-and-slicing.md).

### Spoolman
A separate open-source filament/spool inventory tracker Themis can optionally sync with.
See [Spoolman](../integrations/spoolman.md).

### STL
The older, simpler 3D model format — raw geometry only. No plates, no settings, no colours.

### Thin preset
A small profile that declares `inherits` from a vendor profile and overrides only the keys you
changed. The **recommended** way to add custom settings, because it keeps picking up vendor
updates. Contrast with *flattening*, which freezes a copy.

### Volume
Docker's persistent storage. Containers are disposable; volumes are where the data that must
survive lives. `docker compose down -v` deletes them.

### Webhook
An HTTP POST your server sends to a URL you choose when something happens — the "don't call
us, we'll call you" of integrations. Themis fires them on job events.
