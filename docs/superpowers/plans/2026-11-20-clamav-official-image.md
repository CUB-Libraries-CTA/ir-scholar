# ClamAV Sidecar + Clamby Scanning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop compiling ClamAV in our image. Run `clamd` as a sidecar container (the official `clamav/clamav` image) next to the Sidekiq worker. Make Hyrax actually scan uploads through it using the `clamby` gem.

**Architecture:**
- **App image.** Keep only the `clamdscan` client. It's copied out of the official image with `COPY --link --from=clamav`, the same way `develop` copies NodeJS from `node:$NODE_VERSION-alpine`. The image no longer contains `clamd`, `freshclam`, or the signature DB.
- **Sidecar.** Runs the official image unmodified. The pod shares one `emptyDir` volume, mounted at `/tmp` in the sidecar (where it creates `clamd.sock`) and at `/run/clamav` in the worker.
- **Scanning.** `Scholar::ClambyVirusScanner` runs `clamdscan --fdpass`. The worker opens each upload and passes the open file descriptor over the socket, so `clamd` never needs to read the worker's filesystem.

**Tech Stack:** Docker BuildKit multi-stage, Alpine 3.16 (`ruby:2.7.8-alpine3.16`), official `clamav/clamav` Alpine image (`Cisco-Talos/clamav-docker`, 1.4 entrypoint), Kubernetes (production; manifests live outside this repo), Hyrax 3.6.0, `clamby` 1.6.11, RSpec (rspec-rails 5.1).

**Spec:** The user's requests in this session:
1. "install ClamAV on the docker images the same way NodeJS is installed on the `develop` branch Dockerfile instead of compiling it ourselves" (pattern: commit `886d887` on `develop`)
2. use `clamby` so Hyrax actually scans
3. "run ClamAV in a sidecar"

## Global Constraints

- Branch from `origin/develop`, not `feature-bulkrax`. Branch name: `feature-clamav-sidecar`.
- `ARG CLAMAV_VERSION=1.4` goes next to `ARG NODE_VERSION=22`. The sidecar uses the same tag, `clamav/clamav:1.4`; keep the two in sync. Before merging, confirm the current LTS at https://docs.clamav.net/faq/faq-eol.html. 1.0 (`develop` compiles 1.0.9) is EOL.
- The runtime base stays `ruby:$RUBY_VERSION-alpine$ALPINE_VERSION` (3.16). Don't change Ruby, Alpine, or the `node` stage.
- Only the `hyrax-worker` image ships the client: `/opt/clamav/{bin,lib}` (`clamdscan` plus its own shared libraries, isolated because the official image needs OpenSSL 3.4+ and our Alpine 3.16 has 3.0), a `/usr/local/bin/clamdscan` wrapper that sets `LD_LIBRARY_PATH=/opt/clamav/lib`, and a client-only `/etc/clamav/clamd.conf` containing exactly `LocalSocket /run/clamav/clamd.sock`. `hyrax-web` has no ClamAV at all.
- No `clamd`, `freshclam`, `clamscan`, signature DB, or `clamav` user in either image.
- Socket contract: the sidecar creates `/tmp/clamd.sock` (the official image default; don't override it), and the worker sees it at `/run/clamav/clamd.sock` through the shared `emptyDir`.
- The sidecar goes only on the **worker** Deployment. Scanning happens in `IngestJob` on Sidekiq: `Hyrax::VirusCheck` validates only when a new `original_file` is attached. Web pods don't get a sidecar.
- The Clamby scanner is used only when `Rails.env.production?`. Development (`:inline` jobs) and test keep Hyrax's null scanner.
- Scanning fails closed and loudly. If the scanner can't run (sidecar down or not ready, socket missing, file missing), raise so the Sidekiq job errors and retries. Never report clean, and never report infected because of an infrastructure error.
- Gem: `gem 'clamby', '~> 1.6'` (1.6.11; no Ruby version floor, no runtime deps).

## Review Focus

1. **Sidecar not ready, restarting, or absent.** The first start downloads the DB, and a cold `clamd` takes about a minute to load. A scan during that window must raise a `Clamby::Error`. It must not report clean, and it must not report infected, which is Clamby 1.6.11's default on exit code 2. Tested in Task 2 Step 1 (unit) and Task 2 Step 7 (container, no sidecar).
2. **Root-owned `0600` upload tempfiles in a different container from `clamd` (UID 100).** A path-based scan fails with a permission error; `--fdpass` must work across the container boundary. Tested in Task 1 Step 6.
3. **Socket path mismatch.** The sidecar writes `/tmp/clamd.sock` and the worker reads `/run/clamav/clamd.sock`. Every container test mounts the shared volume exactly as the Kubernetes snippet in Task 3 does, so a path drift fails the test.
4. **ABI drift.** The official image's Alpine (`alpine:latest`) is newer than our 3.16. The build-time `ldd`/`--help` check on `clamdscan` fails `docker build` on a missing library or symbol (Task 1 Step 4).
5. **Architecture.** The Alpine `clamav/clamav` tags may be amd64-only, and the dev machine is arm64. Every build and run uses `--platform linux/amd64`.

---

### Task 1: App image ships only `clamdscan`, copied from the official image

**Files:**
- Modify: `Dockerfile`. Line numbers on `develop`: header lines 1–5, `clamav-builder` stage lines 7–46, `RUNTIME_APK_PACKAGES` lines 153–154, ClamAV user + `COPY` block lines 183–193.

**Interfaces:**
- Produces: `/usr/bin/clamdscan` runs in both `hyrax-web` and `hyrax-worker`, and by default talks to `/run/clamav/clamd.sock`.

- [ ] **Step 1: Branch and baseline**

```bash
git switch -c feature-clamav-sidecar origin/develop
docker build --platform linux/amd64 --target hyrax-worker -t scholar-worker:clamav-before .
docker image ls scholar-worker:clamav-before --format '{{.Size}}'
```
Expected: the build succeeds (slowly, with the Rust + cmake compile). Note the size.

- [ ] **Step 2: Start a local sidecar the same way the pod will**

```bash
docker volume create clamsock
docker run -d --name clamav-sidecar --platform linux/amd64 -v clamsock:/tmp clamav/clamav:1.4
docker logs -f clamav-sidecar   # wait for the socket/"clamd" startup lines, then Ctrl-C
```
Expected: the logs show `clamd` started. `docker exec clamav-sidecar ls -l /tmp/clamd.sock` shows a socket (`s` in the mode).

- [ ] **Step 3: Write the failing test**

```bash
EICAR='X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*'
docker run --rm --platform linux/amd64 -v clamsock:/run/clamav -e EICAR="$EICAR" scholar-worker:clamav-before sh -c '
  umask 077; printf "%s" "$EICAR" > /tmp/eicar.txt
  clamdscan --no-summary --fdpass /tmp/eicar.txt; echo "exit=$?"'
```
Expected: a failure to connect, because the baseline image's compiled-in config doesn't point at `/run/clamav/clamd.sock`, and `exit=2`. Record the exact error.

- [ ] **Step 4: Edit the `Dockerfile`**

1. Header: add `ARG CLAMAV_VERSION=1.4` under `ARG NODE_VERSION=22`. Under the `node` `FROM`, add `FROM clamav/clamav:$CLAMAV_VERSION AS clamav`.
2. Delete the "Stage 1a: ClamAV Builder" section, from its banner through the `freshclam` `RUN`. Rename "Stage 1b" to "Stage 1".
3. Add `fts libbz2 libcurl libstdc++` to `RUNTIME_APK_PACKAGES`. These are the official image's runtime deps that we don't already install; `libclamav` links them. Leave the existing entries alone.
4. Delete `RUN addgroup -S clamav && adduser -S -G clamav clamav` and all ten `COPY --link --from=clamav-builder` lines. Replace them with:
   - `COPY --link --from=clamav /usr/bin/clamdscan /usr/bin/clamdscan`
   - `COPY --link --from=clamav /usr/lib/libclam* /usr/lib/`
   - one `RUN` that does, in order:
     - `mkdir -p /etc/clamav /run/clamav`
     - `printf 'LocalSocket /run/clamav/clamd.sock\n' > /etc/clamav/clamd.conf`
     - `! ldd /usr/bin/clamdscan | grep -q 'not found'`
     - `clamdscan --help >/dev/null`

     Use `--help` because `--version` contacts `clamd`. musl resolves every symbol at program start, so this catches missing symbols too.
   - If `ldd` reports `libfreshclam` missing, add `COPY --link --from=clamav /usr/lib/libfreshclam* /usr/lib/` and say so in the commit message. Don't copy it pre-emptively.

- [ ] **Step 5: Build; the load check must pass**

```bash
docker build --platform linux/amd64 --target hyrax-worker -t scholar-worker:clamav-after .
docker build --platform linux/amd64 --target hyrax-web -t scholar-web:clamav-after .
docker image ls scholar-worker:clamav-after --format '{{.Size}}'
```
Expected:
- both builds succeed
- the image is clearly smaller than the baseline (no DB, which is about 200–300 MB, and no compiled ClamAV)

On `Error relocating`, `symbol not found`, or `not found` from `ldd`, stop and report the library or symbol. Don't swap system libraries.

- [ ] **Step 6: Run the Step 3 command against `scholar-worker:clamav-after`, then once more without `--fdpass`**

Expected:
- with `--fdpass`: `Eicar-Test-Signature FOUND`, `exit=1`
- without `--fdpass`: an access/permission error and `exit=2`, because `clamd` in the sidecar can't open the worker's file

This pins Review Focus 2 and 3.

- [ ] **Step 7: The image no longer contains the server side, and Rails still boots**

```bash
docker run --rm --platform linux/amd64 scholar-worker:clamav-after sh -c 'for b in clamd freshclam clamscan; do command -v $b || echo "no $b"; done; ls /var/lib/clamav /usr/lib/clamav 2>&1; cat /etc/clamav/clamd.conf'
for t in before after; do docker run --rm --platform linux/amd64 scholar-worker:clamav-$t sh -c 'SCHOLARS_SECRET_KEY_BASE=x bundle exec rails runner "puts :ok" 2>&1 | tail -3'; done
```
Expected:
- `no clamd`, `no freshclam`, `no clamscan`, and both directories missing
- the config is exactly `LocalSocket /run/clamav/clamd.sock`
- both `rails runner` calls print the same thing: `ok`, or the same DB/Solr connection error

- [ ] **Step 8: Commit**

```bash
git add Dockerfile
git commit -m "Ship only clamdscan in the app image, copied from the official clamav/clamav image"
```

---

### Task 2: Scan uploads with Clamby in production

**Files:**
- Modify: `Gemfile`, `Gemfile.lock` (`gem 'clamby', '~> 1.6'`)
- Create: `app/services/scholar/clamby_virus_scanner.rb`
- Create: `config/initializers/clamby.rb`
- Modify: `config/initializers/hyrax.rb` (inside the `Hyrax.config do |config|` block)
- Delete: `config/initializers/clamav.rb` (it does nothing; there's no `ClamAV` constant)
- Test: `spec/services/scholar/clamby_virus_scanner_spec.rb`

**Interfaces:**
- Consumes: Task 1's `clamdscan` and `/etc/clamav/clamd.conf`. The sidecar socket is reachable at `/run/clamav/clamd.sock`.
- Produces: `Scholar::ClambyVirusScanner < Hyrax::VirusScanner`. `Scholar::ClambyVirusScanner.infected?(path) -> Boolean`; it logs a warning when a virus is found and raises on any failure to scan. In production, `Hyrax.config.virus_scanner == Scholar::ClambyVirusScanner`.

- [ ] **Step 1: Write the failing spec** `spec/services/scholar/clamby_virus_scanner_spec.rb` (`require 'rails_helper'`)

```ruby
RSpec.describe Scholar::ClambyVirusScanner do
  let(:path) { '/tmp/upload.pdf' }

  it 'is a Hyrax virus scanner' do
    expect(described_class.new(path)).to be_a(Hyrax::VirusScanner)
  end

  it 'reports infected when Clamby finds a virus' do
    allow(Clamby).to receive(:virus?).with(path).and_return(true)
    expect(described_class.infected?(path)).to be true
  end

  it 'reports clean when Clamby finds nothing' do
    allow(Clamby).to receive(:virus?).with(path).and_return(false)
    expect(described_class.infected?(path)).to be false
  end

  it 'raises when Clamby could not scan (nil) instead of reporting clean' do
    allow(Clamby).to receive(:virus?).with(path).and_return(nil)
    expect { described_class.infected?(path) }.to raise_error(Scholar::ClambyVirusScanner::ScanError)
  end

  it 'lets Clamby errors (e.g. sidecar down) propagate so the job retries' do
    allow(Clamby).to receive(:virus?).and_raise(Clamby::ClamscanClientError)
    expect { described_class.infected?(path) }.to raise_error(Clamby::ClamscanClientError)
  end
end
```

- [ ] **Step 2: Run it; it fails**

Run: `bundle exec rspec spec/services/scholar/clamby_virus_scanner_spec.rb`
Expected: FAIL, `uninitialized constant Scholar::ClambyVirusScanner`. If the local test environment can't boot (no MySQL/Solr), use the `test` service in `misc/docker-compose.yml` and say which you used.

- [ ] **Step 3: Implement**

- `Gemfile`: `gem 'clamby', '~> 1.6'`, then `bundle install`.
- `app/services/scholar/clamby_virus_scanner.rb`: `module Scholar; class ClambyVirusScanner < Hyrax::VirusScanner`.
  - Define `class ScanError < StandardError`.
  - `#infected? -> Boolean` calls `Clamby.virus?(file)`:
    - `nil` → raise `ScanError` with the path
    - `true` → call the inherited private `warning("A virus was found in #{file}")` and return `true`
    - `false` → return `false`
- `config/initializers/clamby.rb`: `Clamby.configure(daemonize: true, fdpass: true, error_clamscan_client_error: true, output_level: 'low')`. Keep the other defaults (`check: true`, `error_clamscan_missing: true`, `error_file_missing: true`).
  - Without `error_clamscan_client_error`, Clamby 1.6.11 returns `true` (infected) on `clamdscan` exit code 2. Every upload during a sidecar restart would then fail validation instead of retrying.
- `config/initializers/hyrax.rb`: inside the existing config block, add `config.virus_scanner = Scholar::ClambyVirusScanner if Rails.env.production?`.
- Delete `config/initializers/clamav.rb`.

- [ ] **Step 4: Run the spec; it passes**

Run: `bundle exec rspec spec/services/scholar/clamby_virus_scanner_spec.rb`
Expected: 5 examples, 0 failures.

- [ ] **Step 5: Rebuild the worker image**

```bash
docker build --platform linux/amd64 --target hyrax-worker -t scholar-worker:clamav-after .
```
Expected: the build succeeds, and `clamby 1.6.x` is installed.

- [ ] **Step 6: End-to-end through Hyrax's own service, with the Task 1 Step 2 sidecar running**

```bash
docker run --rm --platform linux/amd64 -v clamsock:/run/clamav -e EICAR="$EICAR" scholar-worker:clamav-after sh -c '
  umask 077; printf "%s" "$EICAR" > /tmp/eicar.txt; printf clean > /tmp/clean.txt
  SCHOLARS_SECRET_KEY_BASE=x bundle exec rails runner "
    p Hyrax.config.virus_scanner
    p Hyrax::VirusCheckerService.file_has_virus?(%q(/tmp/eicar.txt))
    p Hyrax::VirusCheckerService.file_has_virus?(%q(/tmp/clean.txt))"'
```
Expected: `Scholar::ClambyVirusScanner`, `true`, `false`. If `rails runner` can't boot without DB/Solr (see Task 1 Step 7), run it in the `misc/docker-compose.yml` stack with this image plus the sidecar and shared volume, and report which you used.

- [ ] **Step 7: No sidecar → raises, doesn't decide**

```bash
docker stop clamav-sidecar
```
Then run the Step 6 command again, checking only `/tmp/clean.txt`.
Expected: a `Clamby::ClamscanClientError` or `Clamby::ClamscanMissing` backtrace, not `true`/`false`. Afterwards, run `docker start clamav-sidecar` and re-run Step 6 to confirm it recovers.

- [ ] **Step 8: Commit**

```bash
git add Gemfile Gemfile.lock app/services/scholar/clamby_virus_scanner.rb config/initializers/clamby.rb config/initializers/hyrax.rb spec/services/scholar/clamby_virus_scanner_spec.rb
git rm config/initializers/clamav.rb
git commit -m "Scan uploads with Clamby via the clamd sidecar in production"
```

---

### Task 3: Document the worker sidecar for Kubernetes

**Files:**
- Modify: `misc/production-deploy.md`. Add a `## ClamAV sidecar (worker)` section. The worker Deployment manifest itself lives outside this repo.

**Interfaces:**
- Consumes: the socket contract from Global Constraints; `CLAMAV_VERSION` from Task 1.
- Produces: a copy-pasteable pod-spec fragment for the worker Deployment.

- [ ] **Step 1: Write the section.** It contains:

  - **The fragment.** It adds:
    - `volumes: [{name: clamav-socket, emptyDir: {}}]`
    - a sidecar named `clamav`:
      - `image: clamav/clamav:1.4`
      - no `command`/`args`, so the official `/init` runs
      - `volumeMounts: [{name: clamav-socket, mountPath: /tmp}]`
      - `resources.requests.memory: 1536Mi`, `resources.limits.memory: 3Gi` (a DB reload briefly doubles memory)
      - `startupProbe` and `readinessProbe` with `exec: {command: ["clamdcheck.sh"]}`. The startupProbe uses `periodSeconds: 10` and `failureThreshold: 60`, which allows 10 minutes for the first DB download and load.
    - on the existing worker container: `volumeMounts: [{name: clamav-socket, mountPath: /run/clamav}]`

  - **Native sidecar (Kubernetes ≥ 1.29).** Prefer declaring the sidecar under `initContainers` with `restartPolicy: Always`. Kubernetes then starts it first and waits for its `startupProbe` before starting Sidekiq. On older clusters, list it under `containers`. Sidekiq may then start first, and early ingest jobs fail with `Clamby::ClamscanClientError` and retry. That's safe, just noisier.
  - **Keep in sync.** The sidecar tag and the Dockerfile's `CLAMAV_VERSION` should match.
  - **Don't add the sidecar to the web Deployment.**
  - **Network exposure.** The official config also listens on TCP 3310 on the pod IP, and its probes rely on that. Nothing in the app uses it. Restrict it with a NetworkPolicy if the namespace isn't already locked down.

- [ ] **Step 2: Check that the fragment is valid YAML with the expected structure**

Copy the fragment into a minimal `Deployment` in the scratchpad (not the repo). If `kubectl` is available, run `kubectl apply --dry-run=client -f <file>`. Otherwise run `ruby -ryaml -e 'YAML.load_file(ARGV[0])' <file>`.
Expected: `deployment.apps/... created (dry run)`, or no output from the Ruby parse.

- [ ] **Step 3: Commit**

```bash
git add misc/production-deploy.md
git commit -m "Document the ClamAV sidecar for the worker Deployment"
```

---

## Notes for the reviewer (decisions to surface, not to act on)

- **Why a shared socket rather than TCP + `--stream`.** `--stream` copies every file to `clamd` and is capped by `StreamMaxLength`, which defaults to 100 MB. Larger uploads would fail to scan and retry forever. `--fdpass` over the shared socket avoids both the copy and the cap. Overriding the sidecar's `LocalSocket` with `CLAMD_CONF_LocalSocket` isn't clean: the 1.4 entrypoint appends a second `LocalSocket` line rather than replacing the active one. Hence the trick of mounting the volume at `/tmp` in the sidecar.
- **Large files still go through unscanned.** `clamd`'s `MaxFileSize` (100 MB) and `MaxScanSize` (400 MB) defaults still apply. Files over `MaxFileSize` are not scanned at all and are reported clean; scanning stops at `MaxScanSize` of data read. Raise them with `CLAMD_CONF_MaxFileSize` / `CLAMD_CONF_MaxScanSize` on the sidecar (the official build doesn't set either one, so the entrypoint's override lands without creating a duplicate active line), at the cost of memory and time per scan. Your call.
- **What happens to an infected file.** `Hyrax::VirusCheck` adds a validation error ("Failed to verify uploaded file is not a virus"), so the FileSet save fails in `IngestJob`. There's no quarantine or user notification beyond that. That's unchanged Hyrax behavior.
- **Outbound network.** The sidecar's `freshclam` needs outbound HTTPS to `database.clamav.net`, both for the first DB download (if the image's DB is missing) and for daily updates.
- **Other files.**
  - `misc/docker-compose.yml` (local dev) isn't changed. Dev uses inline jobs and the null scanner.
  - `Dockerfile-maintenance-fix` needs no changes.
  - `feature-bulkrax` picks this up on its next `develop` merge, and Bulkrax imports, which run in Sidekiq, get scanned too.
- **Cleanup.** After testing: `docker rm -f clamav-sidecar; docker volume rm clamsock`.
