# FIRE Modeling

An offline, self-contained macOS app for exploring **Financial Independence /
Retire Early (FIRE)** plans with Monte Carlo analysis.

This repository is a **public release snapshot** for downloading the app and
inspecting the runtime source. It intentionally contains the files needed for
the application and its build smoke checks, not the private development
history, workstream logs, prompts, or internal audit archive.

## Download

The current public candidate is:

`v3.0-prephase2-public-1`

Download the universal2 bundle from the [GitHub Release](https://github.com/LinjieW/FIRE/releases/tag/v3.0-prephase2-public-1):

`FIRE-Modeling-v3.0-prephase2-public-1-macos-universal2.zip`

The bundle runs on Apple Silicon and Intel Macs. It includes its own Python,
NumPy, and web UI; no separate Python installation or account is required.

### Verify the download

After downloading, verify the SHA-256 recorded in the Release notes:

```bash
shasum -a 256 FIRE-Modeling-v3.0-prephase2-public-1-macos-universal2.zip
```

### First launch

The app is ad-hoc signed for local sharing. On first launch after downloading
or AirDropping it, use **right-click → Open → Open** if macOS Gatekeeper asks
for confirmation. Then double-clicking works normally.

## Highlights

- **Offline by default.** The UI talks to the bundled server over the Mac's
  loopback interface. The app does not require an account or an external API.
- **Universal2 desktop bundle.** One download supports Apple Silicon and Intel.
- **Monte Carlo planning.** Explore FIRE timing, sustainable spending,
  success probabilities, portfolio paths, and milestone distributions.
- **Decision tools.** Compare withdrawal strategies, sensitivity and stress
  scenarios, Roth-conversion ranges, goal-seeking, housing choices, and
  relocation assumptions.
- **Local continuity.** Plans, drafts, run snapshots, and timeline information
  stay on the local machine. No cloud sync is provided.
- **Transparent assumptions.** The UI includes limitations and provenance for
  tax, healthcare, Social Security, housing, and return assumptions.

## What this release is (and is not)

This is a **pre-Phase-2 candidate**, not a promise of universal equivalence,
tax advice, investment advice, or a GA/enterprise release. It is the frozen
universal2 candidate corresponding to the behavior-neutral pre-Phase-2 source
split. The candidate was built and smoke-tested in an isolated disposable
worktree; it was not used to replace the maintainer's installed app.

The model remains an approximation. Tax, ACA/IRMAA, Social Security, mortality,
housing, China/US relocation, and return assumptions have explicit limits in
the app. Results are scenario analysis, not a forecast or recommendation.

## Source snapshot

The public source contains the runtime and build inputs used by the candidate:

- `engine/` — lifecycle, tax, returns, rule-pack, and housing model code;
- `server/` — local HTTP app, persistence, migration, recovery, and report
  adapters;
- `web/` — the bundled browser UI;
- `build-app.sh`, the dependency lock, PyInstaller entry point, and the
  identity/build helpers;
- a small set of regression, JavaScript, frozen-bundle, and UI smoke checks.

The public snapshot is intentionally squashed onto the repository's initial
MIT-licensed commit. Internal development history and operational documents
remain outside this public repository.

## Building from source (maintainer-oriented)

On a compatible macOS build host with the pinned universal2 toolchain and the
locally merged universal2 NumPy wheel available:

```bash
BUILD_ONLY=1 ./build-app.sh
```

The script validates the JavaScript, regression, frozen-bundle, signing, and
universal2 gates and leaves a candidate under `.build/`. It does not install or
replace an existing app.

## Local data and privacy

Plans and imported data are intended to remain local to the machine. The app
does not upload broker CSV contents, Social Security statement data, plans, or
run results. Do not put sensitive personal data into a public issue or pull
request.

## License

MIT. See [LICENSE](LICENSE).
