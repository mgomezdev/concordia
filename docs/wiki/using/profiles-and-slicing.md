# Profiles & Slicing

How a model becomes machine instructions, and how to get your own OrcaSlicer settings into the
stack.

---

## What slicing is

A 3D model describes a *shape*. A printer needs *instructions* — where to move, how fast, how
hot, layer by layer. Converting one to the other is **slicing**, and the output is **gcode**.

The same model sliced for two different printers produces two different gcode files. That's
why Themis slices per-printer, at the moment a printer claims the job — not once at upload.

---

## Who does what

| Step | Who |
|---|---|
| You pick printers, profile, filament | **Themis** |
| Job waits for an eligible printer | **Themis** queue engine |
| A printer claims it | **Themis** |
| Resolve preset names → catalog UUIDs | **Themis** |
| Assemble the 3MF, resolve profile inheritance, run OrcaSlicer | **Laminus** |
| Upload gcode and start the print | **Themis** |

Themis doesn't run OrcaSlicer itself. It hands the whole slice to Laminus over HTTP and gets
gcode back. That's why Laminus must be healthy before Themis will even start.

Sequence diagrams, including error paths: `concordia/docs/slicing-flow.md`.

---

## The profile catalog

Laminus ships OrcaSlicer's entire bundled vendor catalog. On the build this wiki was written
against:

| Kind | Count | What it controls |
|---|---|---|
| **Machine** | 1,545 | The printer — bed size, nozzle, kinematics. |
| **Process** | 3,201 | How to print — layer height, speeds, infill. |
| **Filament** | 6,475 | The material — temperatures, flow, cooling. |

(705 more were skipped as unusable — normal.)

Every slice needs one of each. Assigning a **machine** preset to a printer is
[step 3 of the add-printer wizard](add-a-printer.md#step-3--profile); process and filament are
chosen per job.

Catalog counts come from the Laminus health endpoint — see
[Verify, step 4](../setup/04-verify-it-worked.md#4-laminus-has-built-its-catalog).

---

## Adding your own presets

The bundled catalog covers stock configurations. If you've tuned settings in OrcaSlicer — a
custom filament, a modified process — you'll want those here too.

### The right way: a thin preset that inherits

Drop a JSON file into the Laminus config volume under
`user/default/{machine,process,filament}/`, containing an `inherits` key naming a vendor
profile plus **only the keys you're changing**:

```json
{
  "type": "filament",
  "name": "My Tuned PETG",
  "inherits": "Generic PETG",
  "filament_flow_ratio": "0.94",
  "nozzle_temperature": ["245"]
}
```

Laminus resolves the inheritance chain itself, at catalog-build and slice time. So when
OrcaSlicer ships an updated `Generic PETG`, your override rides along on top of the new
version automatically.

Then **Settings → Rescan profiles** in Themis (or restart Laminus) to pick it up.

### Uploading via the API

```bash
curl -X POST http://localhost:5000/api/profiles/upload \
  -F "type=filament" \
  -F "file=@MyFilament.json"
```

Laminus isn't exposed on a host port by default, so run this from inside the container or
[publish the port temporarily](#reaching-laminus-directly).

### Flattening — last resort only

Laminus ships `flatten_profiles.py`, which bakes a vendor profile and all its inherited values
into one standalone file. **Prefer inheritance.** A flattened profile is frozen at the
OrcaSlicer version it came from and silently stops receiving vendor fixes. Use it only for a
permanent fork — the vendor profile is going away, or you need to diverge for good.

---

## Changing the OrcaSlicer version

Set `ORCA_VERSION` in `.env` and restart:

```ini
ORCA_VERSION=2.4.2
```

The entrypoint notices the mismatch, downloads the new AppImage, and replaces the cached
install — no image rebuild. Expect a slow start afterwards, and note the catalog will change
with it.

---

## Reaching Laminus directly

By design Laminus has **no host port** — nothing outside the Docker network can reach it. Its
dashboard and API exist but aren't published.

To use them temporarily, add a port mapping to `docker-compose.yml` under `laminus`:

```yaml
    ports:
      - "5000:5000"
```

Then `docker compose up -d laminus` and open <http://localhost:5000>.

**Remove it when you're done.** Laminus has no authentication whatsoever — it will slice, and
read and write profiles, for anyone who can reach it.

---

## When slicing fails

A slice failure **blocks** the job rather than failing it — another printer may still be able
to run it, and Laminus retries geometry-only on the first failure. Open the job to see the
slice error.

The usual causes:

| Cause | Fix |
|---|---|
| Printer has no machine profile | Fleet → edit printer → set make/model/nozzle. |
| Catalog not loaded | [Check Laminus health](../setup/04-verify-it-worked.md#4-laminus-has-built-its-catalog). |
| Model doesn't fit the bed | Scale it, or use a bigger printer. |
| Filament preset incompatible with the machine | Pick one meant for that nozzle/machine. |
