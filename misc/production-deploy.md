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
* Files larger than `MaxFileSize` (default 100M) are not scanned at all and are
  reported clean. Scanning also stops once `MaxScanSize` (default 400M) of data has
  been read, e.g. from an archive's contents, and the file is reported clean. Raise
  them with `CLAMD_CONF_MaxFileSize` / `CLAMD_CONF_MaxScanSize` env vars on the
  sidecar if needed.


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
