# Projects & Orders

For when "one model, one job" isn't enough — a set of parts, in quantities, that should be
packed onto plates and printed together.

---

## The difference

| | What it is |
|---|---|
| **Project** | A **bill of materials** — parts, quantities, requirements. A plan for what to make. |
| **Order** | A **commitment** — customer or internal, with a parts checklist tracking what's delivered. |

A Project *generates* jobs and a linked Order in one step. The Order then tracks fulfilment as
those jobs complete.

Use a Project when you'd otherwise create the same job eight times by hand.

---

## Building a project

**Projects → new project**, then assemble the BOM. Per part:

| Field | Purpose |
|---|---|
| Part / model | Which model from the library. |
| Quantity | How many. |
| Filament requirement | Type and colour. |
| Printer eligibility | Which machines may run it. |

You can also attach reference links to the project.

---

## Generating

**Generate** does the work:

1. **Packs** parts and quantities across plates, fitting them to the eligible printers' beds.
2. **Creates the jobs** for those plates.
3. **Creates an Order** linked to the project.

From there the jobs behave like any other — they sit in the shared queue and get claimed by
eligible printers. See [Your First Print](first-print.md).

The project's detail view tracks linked-job status and the parts checklist, so you can see
progress without reading the queue.

---

## Orders

An order carries a parts checklist and can be created, edited, and put **on hold**. Holding is
useful when a customer pauses — the order stops pulling work without you cancelling the jobs.

Jobs can be attached to an order individually too, via the **Order link** field on New Job.

> **Note on navigation.** The Themis README describes a top-level **Orders** screen. There
> isn't one in this build's sidebar — orders are reached through Projects. The underlying
> orders API is fully present (`/api/v1/orders`), so this is a UI navigation gap, not missing
> functionality. See [Where the READMEs are wrong](../reference/tips-and-faq.md#where-the-readmes-are-wrong).

---

## From Ordinus

A Gridfinity layout designed in Ordinus arrives here as a project — Ordinus uploads the STLs
and creates a Themis project with the parts as items, plus a backlink to the layout.

One thing to know: **Ordinus never assigns filament.** Any layout you send will need filament
set on its parts in Themis before jobs can be generated. See
[Ordinus / Gridfinity](ordinus-gridfinity.md#sending-to-themis).

Re-sending the same layout resumes the existing project rather than creating a duplicate, and
models shared between layouts are deduplicated.
