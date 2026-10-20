# Print Garden Wiki

The home-workshop 3D printing stack: design a part, queue it, slice it with your real
OrcaSlicer presets, and print it — on one or many printers.

**New here? Start with [Quick Start](setup/00-quick-start.md) if you're comfortable with a
terminal, or [Prerequisites](setup/01-prerequisites.md) if you'd like to be walked through it.**

---

## The four pieces

| Name | What it does | You open it at |
|---|---|---|
| **Themis** | The print farm manager — queue, printers, files, jobs. The app you'll use daily. | `http://localhost:8001` |
| **Laminus** | The slicer engine. Runs OrcaSlicer headless. You never open it directly. | internal only |
| **Ordinus** | Gridfinity drawer-layout designer. Sends parts to Themis to print. | `http://localhost:3002` |
| **Concordia** | The conductor. Not an app — the config that starts the other three together. | *n/a* |

If you only remember one thing: **Themis is the app, Concordia is how you start it.**

---

## Setup

| Page | What's in it |
|---|---|
| [Quick Start](setup/00-quick-start.md) | The whole install in six commands, for people who already use Docker. |
| [Prerequisites](setup/01-prerequisites.md) | Installing Docker Desktop, opening a terminal, getting the files. No assumptions. |
| [First Run](setup/02-first-run.md) | Starting the stack and knowing when it's actually ready. |
| [First Login & Security](setup/03-first-login.md) | **Read before you click anything in Settings.** The one mistake that locks you out. |
| [Verify It Worked](setup/04-verify-it-worked.md) | A checklist to confirm all three services are healthy. |
| [Troubleshooting](setup/05-troubleshooting.md) | Real failures with real fixes. |

## Using the stack

| Page | What's in it |
|---|---|
| [Themis Tour](using/themis-tour.md) | Every screen, what it's for. |
| [Add a Printer](using/add-a-printer.md) | The four-step wizard; what each vendor needs. |
| [Profiles & Slicing](using/profiles-and-slicing.md) | How your OrcaSlicer presets get used, and how to add your own. |
| [Your First Print](using/first-print.md) | Upload → queue → slice → print, and what "blocked" means. |
| [Projects & Orders](using/projects-and-orders.md) | Batch a bill of materials into jobs. |
| [Ordinus / Gridfinity](using/ordinus-gridfinity.md) | Design a drawer, generate a BOM, send it to Themis. |

## Integrations

| Page | What's in it |
|---|---|
| [Spoolman](integrations/spoolman.md) | Sync filament inventory. |
| [Notifications](integrations/notifications.md) | Get told when a job finishes or breaks. |

## Reference

| Page | What's in it |
|---|---|
| [Architecture](reference/architecture.md) | How the four pieces talk to each other. |
| [Configuration](reference/configuration.md) | Every setting, every compose file, every volume. |
| [Glossary](reference/glossary.md) | Slicing, 3MF, gcode, AMS, sidecar — in plain words. |
| [Tips & FAQ](reference/tips-and-faq.md) | Things worth knowing that don't fit elsewhere. |

---

## About this wiki

Every command, screenshot, and claim here was verified against a real stack brought up from
the published `:latest` Docker images on **2026-09-20**. Where the individual app READMEs
disagree with what the software actually does, this wiki follows the software — see
[Tips & FAQ](reference/tips-and-faq.md#where-the-readmes-are-wrong).

> **On versions.** The stack currently runs whatever `:latest` points to, so a future pull
> may not match these screenshots exactly. Pinning is [tracked separately](reference/configuration.md#pinning-versions).
