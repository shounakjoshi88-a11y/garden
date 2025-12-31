# Changelog

All notable changes to `garden`.

## 1.0.0 - 2026-10-21

### Added

- `garden split` - splits a batch of working-tree changes into logical
  conventional commits, classified by path pattern and diff content.
- `garden day` - runs a bounded set of real checks against monitored
  repositories and commits each artifact separately.
- `garden backfill` - fills missing days with real reports committed under a
  backdated author date. Never rewrites existing history.
- `garden status` - renders the contribution calendar locally from your clones.
- `garden push-fleet` - pushes the ops repository and every monitored
  repository.
- `garden install-actions` - writes the scheduled workflow.
- Checks: `metrics`, `todos`, `tests`, `lint`, `coverage`, `deps`, `changelog`,
  `notes`, `cards`.
- GitHub Actions workflow: three runs a day, plus `workflow_dispatch` with
  optional backfill inputs.
