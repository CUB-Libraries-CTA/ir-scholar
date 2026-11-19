# etd-loader

## Setting up a Python 3.15 environment

Create a virtual environment named `.venv` in this directory, activate it, and install
the packages listed in `requirements.txt`. Python 3.15 must already be installed.

macOS and Linux:

```sh
cd etd-loader
python3.15 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Windows (PowerShell):

```powershell
cd etd-loader
py -3.15 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

In Windows Command Prompt, activate with `.venv\Scripts\activate.bat` instead.

While the environment is active, `python` runs Python 3.15 with these packages, for
example `python bagit_builder.py -h`. Run `deactivate` to leave it, and activate it again
in each new terminal before running the scripts.

The `.venv` directory isn't committed: Python creates it with its own `.gitignore`.

## Bagging ProQuest zips with `bagit_builder.py`

`bagit_builder.py` turns every ProQuest ETD zip in a directory into a BagIt bag that
Scholar's Bulkrax BagIt importer can load. The zips themselves are never modified.

It needs `safe_unzip.py`, `bulkrax_bag.py`, `console.py`, `tocsv.py` and `xmltos3.py`
in the same directory, and Python 3.12 or newer (the environment above).

### Running it

```sh
python bagit_builder.py /path/to/zips
```

| Option | What it does |
| --- | --- |
| `--contact-name NAME` | Adds `Contact-Name` to each new bag's `bag-info.txt` |
| `--source-organization ORG` | Adds `Source-Organization` to each new bag's `bag-info.txt` |
| `--jobs N` | Number of zips processed at the same time, each in its own process (default 4). Use `--jobs 1` to process them one at a time. |
| `--processes N` | Number of processes used for the checksums of each bag (default 1). bagit hashes each file in one process and starting the processes is slow, so this rarely helps ETD bags, which hold one PDF and a few attachments. |
| `--upload-xml-to-s3` | Uploads each ProQuest record to the `cubl-static` S3 bucket under `samvera/metadata/` and puts its URL in the `replaces` column. Without it, `replaces` is just the file number from the zip name (`etdadmin_upload_123456.zip` → `123456`). |
| `--test` | With `--upload-xml-to-s3`, uploads under `samvera/metadata/test/` instead. Not allowed without `--upload-xml-to-s3`. |

`--upload-xml-to-s3` needs AWS credentials that can write to the bucket, found the usual
way for boto3 (environment variables, `~/.aws/credentials` or `AWS_PROFILE`). If the
upload fails, that zip's folder goes to `failed/`. The record is only uploaded once its
folder has been bagged and validated, so nothing that goes to `failed/`, `rejected/` or
`unaccepted/` for another reason is uploaded.

For example, a test run that uploads to the test location:

```sh
python bagit_builder.py /path/to/zips --source-organization "University of Colorado Boulder" \
    --upload-xml-to-s3 --test
```

Run `python bagit_builder.py -h` for the full details.

### What happens to each zip

Every `<name>.zip` directly inside the directory is processed, several at a time (see
`--jobs`). Zips that would be extracted into the same folder (e.g. `my thesis.zip` and
`my_thesis.zip`) are still processed one after the other, in name order. Hidden files
and subdirectories are ignored.

1. The zip is extracted into `<name>/` next to it. `__MACOSX/` folders and `.DS_Store`
   files are left out, and a zip that wraps everything in one folder is unwrapped.
2. If the folder is already a bag (it has `bagit.txt`), it's only validated and left
   as it is, so it needs its own `metadata.csv` to be imported.
3. Otherwise the ProQuest record (`*_DATA.xml`) at the top of the folder is converted
   into `metadata.csv` for Bulkrax. A folder without a record must already have a
   `metadata.csv` at its top level.
4. Spaces, `:`, `;` and `|` in file and folder names are replaced with underscores,
   because Bulkrax can't attach a file with one of them in its path. This includes the
   folder itself: `my thesis.zip` is extracted into `my_thesis/`.
5. The folder is bagged and validated, and moved into one of the directories below.

Each new bag looks like this. Bulkrax attaches everything in `data/` to the work, so the
record and `metadata.csv` are kept in the bag root:

```
etdadmin_upload_123456/
├── Doe_ucolorado_0051E_12345_DATA.xml   ProQuest record
├── metadata.csv                         Bulkrax metadata
├── bagit.txt, bag-info.txt, manifest-*.txt, tagmanifest-*.txt
└── data/
    ├── Doe_ucolorado_0051E_12345.pdf
    └── Doe_supp/                        attachments, if any
```

### Results

Each extracted folder ends up in one of these directories, created inside the directory
of zips:

| Directory | Meaning | Log inside the folder |
| --- | --- | --- |
| `processed/` | The bag was created and validated, with its `metadata.csv`, or the zip already held a valid bag. The zip is renamed to `<name>.zip.processed`. | none |
| `failed/` | The record was fine but bagging went wrong (see below). The folder holds the zip's original contents. | `bagit_errors.log` |
| `rejected/` | The ProQuest record couldn't be converted into `metadata.csv` (see below). | `bagit_errors.log` |
| `unaccepted/` | The ProQuest record isn't accepted: `DISS_acceptance` is missing or `0`. | `bagit_unaccepted.log` |
| `skipped/` | The zip already held a bag that is already in `processed/`. | `bagit_skipped.log` |

Reasons a folder goes to `rejected/`:

- the record can't be read, or lacks elements the conversion needs (e.g. older records
  without `DISS_repository`)
- no `*_DATA.xml` record and no `metadata.csv`
- more than one record (Bulkrax attaches every file in a bag to each of its works)
- a file the record lists is missing from the zip
- another file ending in `metadata.csv` that Bulkrax could read instead

Reasons a folder goes to `failed/`:

- renaming a file would overwrite another one (e.g. `a b.pdf` next to `a_b.pdf`)
- a bag with the same name is already in `processed/`
- creating or validating the bag failed, or a zip that was already a bag is invalid
- the `--upload-xml-to-s3` upload failed

A summary is printed at the end. The exit code is 0 if nothing failed or was rejected
(unaccepted and skipped zips don't count), 1 otherwise, and 130 if the run was stopped
with Ctrl+C.

A zip that couldn't be extracted at all is left unchanged, with the reason in
`<name>.zip.bagit_errors.log` next to it. It's tried again on the next run.

### Retrying a zip

A zip whose folder is in `failed/`, `rejected/`, `unaccepted/` or `skipped/` isn't
extracted again: the run reports it with the same outcome and writes the reason next to
the zip. Fix the problem, delete that folder (e.g. `rejected/<name>/`) and run the script
again. To redo a zip that reached `processed/`, delete `processed/<name>/` and rename
`<name>.zip.processed` back to `<name>.zip`.

Stopping a run with Ctrl+C cleans up the folders being processed and starts no more
zips. Pressing Ctrl+C again while it cleans up is ignored, so the cleanup always
finishes; to stop it at once anyway, press Ctrl+\ (macOS and Linux). If the script is
killed outright (or the machine shuts down), a half-processed
`<name>/` folder can be left next to the zips. The next run reports it and leaves that
zip alone until the folder is deleted.

### Tests

`test/` has the tests and a benchmark; see `test/README.md`. In short:

```sh
python -m pip install -r test/requirements.txt
python -m pytest test
```

### Importing the bags into Scholar

In Bulkrax, create a new importer with:

- **Parser:** Bagit
- **Metadata file name:** `metadata.csv`
- **Metadata format:** CSV
- **Admin set:** the one the ETDs belong in. The CSV has no `admin_set_id` column, so the
  importer's choice is used.

Then either upload a zip of the bags in `processed/`, or choose "Specify a Path on the
Server" and give the path of a directory containing them.
