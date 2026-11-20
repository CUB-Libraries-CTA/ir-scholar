# Production deployment Notes

## SOLR

Helm chart used

image 6.6.6
storage class set aws-


* Setup intial collection and core

        $ rk get pods -n test-scholar 
        # git clone ir-scholar and cd into repo
        # Upload files to solr pod
        $ rk cp solr/config solr-0:/opt/solr/configset -n test-scholar

* Upload to zookeeper within contianer

        $ solr zk upconfig -n scholar -d configset
        $ solr create -c hydra-prod -n scholar -shards 2


## Running as a non-root user

The web and worker images run as the unprivileged user `scholar` (UID and GID 1000), which
owns only `/data/tmp` and `/data/log` inside the image. Match it in the Deployments: set the
user on the app containers (so Kubernetes rejects them if they'd run as root) and `fsGroup`
on the pod (so mounted volumes are group-writable):

```yaml
spec:
  template:
    spec:
      securityContext:
        fsGroup: 1000
      containers:
        - name: web # or worker
          securityContext:
            runAsNonRoot: true
            runAsUser: 1000
            runAsGroup: 1000
```

Don't put `runAsNonRoot` at the pod level on the worker: it would also apply to the clamav
sidecar, whose official image starts as root and drops to its own `clamav` user.

Volumes that already hold files written by the old root-run images (uploads, derivatives
and anything else mounted under `/data/tmp`) need their ownership changed once, e.g.
`chown -R 1000:1000` from a one-off pod. `fsGroup` changes group ownership on most volume
types when they're mounted, but not on NFS-based volumes such as EFS; those need the chown.


## Google Analytics key

The Google service account key (`*.p12`) is kept out of the images by `.dockerignore`.
`config/analytics.yml` only sets the GA4 tracking ID today, so nothing reads the key. If
Hyrax analytics reporting is turned back on (`privkey_path`, `privkey_secret`,
`client_email` and the other GA4 keys in `config/analytics.yml`), mount the key from a
Kubernetes Secret and point `privkey_path` at the mounted file instead of baking it in:

```yaml
      volumes:
        - name: analytics-key
          secret:
            secretName: scholar-analytics-key
      containers:
        - name: web # and the worker, if it reports analytics
          volumeMounts:
            - name: analytics-key
              mountPath: /etc/scholar/analytics
              readOnly: true
```


## ClamAV sidecar (worker)

Uploads are virus-scanned in the worker (`IngestJob` on Sidekiq) through
`Scholar::ClambyVirusScanner`, which runs `clamdscan --fdpass` against a `clamd`
sidecar. Add this to the **worker** Deployment only; the web image has no ClamAV
client and never scans.

The official image creates its socket at `/tmp/clamd.sock`, so one `emptyDir` is
mounted at `/tmp` in the sidecar and at `/run/clamav` in the worker, where the
worker image's `/etc/clamav/clamd.conf` expects `/run/clamav/clamd.sock`.

```yaml
spec:
  template:
    spec:
      volumes:
        - name: clamav-socket
          emptyDir: {}
      initContainers:
        # Native sidecar (Kubernetes >= 1.29): starts before the worker, and the worker
        # waits for its startupProbe. Keep the tag in sync with CLAMAV_VERSION in the Dockerfile.
        - name: clamav
          image: clamav/clamav:1.4
          restartPolicy: Always
          env:
            # Scan files (and archive contents) up to 1GB; larger ones are reported clean
            # without being scanned. clamd's defaults are 100M / 400M.
            - name: CLAMD_CONF_MaxFileSize
              value: 1024M
            - name: CLAMD_CONF_MaxScanSize
              value: 1024M
          volumeMounts:
            - name: clamav-socket
              mountPath: /tmp
          resources:
            requests:
              memory: 1536Mi
            limits:
              memory: 3Gi # a signature reload briefly holds two databases in memory
          startupProbe:
            exec:
              command: ["clamdcheck.sh"]
            periodSeconds: 10
            failureThreshold: 60 # first start may download the DB, then load it
          readinessProbe:
            exec:
              command: ["clamdcheck.sh"]
            periodSeconds: 30
          # Restarts a hung clamd; scans have no timeout, so a hang would otherwise
          # block ingest jobs. Generous because a busy clamd can be slow to answer PING.
          livenessProbe:
            exec:
              command: ["clamdcheck.sh"]
            periodSeconds: 60
            timeoutSeconds: 10
            failureThreshold: 5
      containers:
        - name: worker # the existing worker container; add only this mount
          volumeMounts:
            - name: clamav-socket
              mountPath: /run/clamav
```

* On clusters older than 1.29, move the `clamav` entry from `initContainers` to
  `containers` and drop `restartPolicy`. Sidekiq may then start before `clamd` is
  ready; ingest jobs in that window raise `Clamby::ClamscanClientError` and retry.
* `freshclam` in the sidecar updates signatures daily and needs outbound HTTPS to
  `database.clamav.net`.
* The official config also has `clamd` listen on TCP 3310 on the pod IP (its
  `clamdcheck.sh` probe uses it). Nothing in the app does; restrict it with a
  NetworkPolicy if the namespace isn't already locked down.
* Files larger than `MaxFileSize` (set to 1024M above) are not scanned at all and
  are reported clean. Scanning also stops once `MaxScanSize` (1024M) of data has
  been read, e.g. from an archive's contents, and the file is reported clean.
* clamd unpacks archives into its temp directory, which is the shared `/tmp`
  `emptyDir`, so scanning a 1GB archive can use about that much of the pod's
  ephemeral storage.


## Scholar Administration set 




## Scholar Style

<div class="navbar-header">
        <button type="button" class="navbar-toggle collapsed" data-toggle="collapse" data-target="#top-navbar-collapse" aria-expanded="false">
          <span class="sr-only">Toggle navigation</span>
          <span class="icon-bar"></span>
          <span class="icon-bar"></span>
          <span class="icon-bar"></span>
        </button>
        <a id="logo" href="/" class="navbar-brand" style="
    width: 350px;
"><img src="https://cdn.colorado.edu/static/brand-assets/live/images/cu-boulder-logo-text-white.svg" alt="Scholar @ University of Colorado Boulder">

</a>
<span class="navbar-brand" style="
    margin-top: 5px;
">Scholar</span>
      </div>







administrative_report_or_publication: "Administrative Report Or Publication"
article: "Article"
conference_proceedings_or_journal: "Conference Proceedings Or Journal"
dataset: "Dataset"
default: "Other Scholarly Content"
eesc_publication: "EESC Publication"
graduate_project: "Graduate Project"
graduate_thesis_or_dissertation: "Graduate Thesis Or Dissertation"
honors_college_thesis: "Honors College Thesis"
open_educational_resource: "Open Educational Resource"
purchased_e_resource: "Purchased e-Resource"
technical_report: "Technical Report"
undergraduate_thesis_or_project: "Undergraduate Thesis Or Project"
