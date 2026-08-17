# Release process

Stable releases use semantic version tags such as `v0.6.1`.

## Prepare

1. Update `custom_components/somfy_io_manager/manifest.json`.
2. Update the `[project]` version in `pyproject.toml`.
3. Add the release notes to `CHANGELOG.md`.
4. Confirm the README compatibility table still matches the required manager
   API, Home Assistant, ESPHome, and validated hardware path.
5. Run every command in the local-checks section of `CONTRIBUTING.md`.
6. Confirm the integration source contains no real identities, credentials,
   keys, recovery payloads, or private network details.

## Publish

1. Push the release commit to `main`.
2. Wait for HACS validation, hassfest, lint, tests, compilation, and archive
   packaging to pass.
3. Create the GitHub release from the validated commit with a matching `vX.Y.Z`
   tag.
4. Wait for the **Release Assets** workflow to attach
   `somfy_io_manager.zip`.
5. Confirm the ZIP contains the integration files at archive root and does not
   contain tests, examples, caches, local configuration, or repository
   metadata.
6. Install the release through HACS on a non-critical Home Assistant instance,
   restart, and verify setup plus one already-paired shutter before broad use.

Do not publish a release that points to test-only firmware, unreleased manager
API behavior, or local dependencies.
