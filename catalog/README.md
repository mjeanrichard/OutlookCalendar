# Private catalog index

`widgets.json` here is the **source** of the catalog entry. The **published**
index lives on the `catalog` branch, at the repo root, written by
`.github/workflows/release.yml` on every release.

It exists so a production install — in particular the Home Assistant add-on,
which has no filesystem you can reach — can install and update these widgets
through the normal Settings → Widgets → Browse flow instead of someone copying
folders into `/data/marketplace/` by hand.

## Pointing an installation at it

**Settings → Server → Marketplace catalog URL**:

    https://raw.githubusercontent.com/mjeanrichard/OutlookCalendar/catalog/widgets.json

Then **Settings → Widgets → Browse** lists the bundle; Install downloads the
release asset, verifies the sha256, validates every `plugin.json` against
the host schema, and drops `outlook_core/`, `outlook_family/` and `outlook_month/` into
`<data_root>/marketplace/`. A restart is always required afterwards.

Note this *replaces* the official catalog rather than adding to it — the host
reads exactly one index URL. Clear the field to go back to dmellok's.

## How publishing works

On each release the workflow takes this file, overwrites **only** the `release`
block (version, tarball URL, sha256 of the asset it just built), copies
`screenshots/` alongside it, and force-pushes the result as a single commit to
the `catalog` branch.

So: edit the entry here, and the next release publishes it. The `release` block
in this file is whatever was last written by hand — the workflow ignores its
contents and overwrites it, so do not bother keeping it current.

Why a separate branch: `github-actions[bot]` cannot push to a protected `main`
on a personal repo. Neither classic protection's `bypass_pull_request_allowances`
nor a ruleset `Integration` bypass actor is accepted there — both return 422.
The `catalog` branch is outside the protection's scope, so the bot can write it
with the default `GITHUB_TOKEN`, no PAT and no weakening of `main`. It also
means the push cannot re-trigger the pipeline: no workflow triggers on that
branch.

The branch is replaced wholesale each time; its history is not meaningful, and
the entry's real history is here on `main`.

## Constraints worth remembering

- `version` is the *index* schema version (must be `1`), not the widget's.
- `description` is capped at 280 characters.
- `folders` must match the tarball's direct children exactly — the workflow
  fails the release if this file disagrees with `BUNDLE_FOLDERS`.
- Screenshots are resolved relative to the index URL, so author them at
  `catalog/screenshots/<id>/lg.png` here and the workflow places them correctly.
- This file is also the dry run for the official catalog PR to
  `dmellok/tesserae-widgets`: same schema, same install path.
