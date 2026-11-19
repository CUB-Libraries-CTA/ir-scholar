# etd-loader tests

Tests for `bagit_builder.py` and the modules it uses, plus a benchmark.

## Running them

With the environment from `../README.md` active, install pytest once, then run the
tests from the `etd-loader` directory:

```sh
python -m pip install -r test/requirements.txt
python -m pytest test
```

The whole suite takes under a minute. To leave out the slower tests (Ctrl+C and the
real ETDs), add `-m "not slow"`.

Nothing is written outside pytest's temporary directories, and nothing is sent to S3:
the S3 tests replace boto3's client with a stub and blank every AWS credential source.

## What's covered

| File | What it checks |
| --- | --- |
| `test_bagit_builder.py` | End-to-end runs on generated zips: which output directory each zip's folder goes to and why, the logs, the summary and exit code, the bag layout, `metadata.csv` values, renaming unsafe characters, re-runs and retries, and that `--jobs 1` and `--jobs 4` give the same result. |
| `test_safe_unzip.py` | `extract_zip` on its own: refusing existing targets, unwrapping, leaving out Finder metadata, and leaving nothing behind when Ctrl+C lands at the worst moments. |
| `test_s3.py` | `--upload-xml-to-s3` and `--test` with S3 stubbed: the `replaces` URL, the prefix, a failed bucket lookup, a failed upload, that only bagged folders are uploaded, and that a bag is still valid after the upload. |
| `test_main_py.py` | `tocsv()` (what `main.py` runs) behaves as the original loader did, compared against `tocsv.py` from commit 8792734. Skipped if git can't read that commit. |
| `test_real_etds.py` | Runs on the real ETDs in `../etds/` (see below). |
| `test_ctrl_c.py` | Ctrl+C at several points of a run, with `--jobs 1` and `--jobs 3`: exit code 130, cleanup, no tracebacks, a summary that matches what's on disk. macOS and Linux only. |
| `test_interrupt_while_recording.py` | Ctrl+C at moments too short to hit by hand: just as a bag lands in `processed/` (the zip is still finished and counted, and the next run fails nothing), and just after extraction (the folder is removed). macOS and Linux only. |

The test zips are built by `helpers.py` (see `make_standard_zips`), which also lists the
outcome each one should get.

## Real ETDs

Put real ProQuest zips in `etd-loader/etds/` and `test_real_etds.py` and
`test_main_py.py` also run on them; with none there, those tests are skipped. The set can
change freely: each zip's expected outcome is worked out from its own record. `etds/` is
in `.gitignore`, because the zips contain personal details.

## Benchmark

`benchmark.py` isn't part of the test run. It times `bagit_builder.py` with different
`--jobs` and `--processes` settings, on fresh copies of the zips each time:

```sh
python test/benchmark.py                     # the zips in etds/
python test/benchmark.py --generate 12 40    # 12 generated zips with 40 MB PDFs
python test/benchmark.py --config "--jobs 1" --config "--jobs 8" --repeats 5
```

## Helper files

- `helpers.py`: builds test zips, runs `bagit_builder.py`, reads its results.
- `conftest.py`: pytest fixtures (the generated zips, a snapshot of `etds/`).
- `s3_stub.py`: runs `bagit_builder.py` with S3 stubbed, in worker processes too.
- `tocsv_runner.py`: runs `tocsv()` with its side effects (rake, mv, logging, S3) stubbed.
- `interrupt_runner.py`: runs `bagit_builder.py` and presses Ctrl+C right after the
  first bag reaches `processed/`.
