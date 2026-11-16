# Releasing cfe to PyPI

Releases publish from GitHub Actions (`.github/workflows/release.yaml`) using PyPI trusted publishing. No API token is stored anywhere.

## One-time setup on pypi.org

The GitHub side is already configured: the `release` environment exists and only allows deployments from tags matching `v*`.

On PyPI, add a trusted publisher for the existing `cfe` project:

1. Sign in at https://pypi.org as an owner of `cfe`.
2. Go to https://pypi.org/manage/project/cfe/settings/publishing/.
3. Under "Add a new publisher", choose **GitHub** and enter:
   - Owner: `jmitchel3`
   - Repository name: `cfe`
   - Workflow name: `release.yaml`
   - Environment name: `release`
4. Click **Add**.

Optional: in the repo's Settings > Environments > `release`, add yourself as a required reviewer so every publish waits for approval.

## Release steps

1. Bump the version in both places (they must match the tag, or the workflow fails):
   - `pyproject.toml`: `version = "X.Y.Z"`
   - `cfe/__init__.py`: `__version__ = "X.Y.Z"`
2. Run `uv lock` so `uv.lock` picks up the new version.
3. In `CHANGELOG.md`, rename `Unreleased` to `X.Y.Z (YYYY-MM-DD)` and start a new empty `Unreleased` section.
4. Open a PR, wait for CI, merge to `main`.
5. Create the release from up-to-date `main`:

   ```bash
   gh release create vX.Y.Z --generate-notes
   ```

Publishing the release triggers `release.yaml`:

- **build**: checks the tag (minus `v`) equals both versions, runs the tests, runs `uv build`, installs the wheel in a clean venv and checks `import cfe.main` does not import Django, then uploads `dist/`.
- **publish**: runs in the `release` environment and uploads `dist/` to PyPI with `pypa/gh-action-pypi-publish` (attestations included).

If the version check fails, delete the release and its tag, fix the versions on `main`, and create the release again.
