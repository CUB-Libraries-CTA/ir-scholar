# frozen_string_literal: true

# clamdscan talks to clamd in the worker's sidecar container over the shared
# /run/clamav/clamd.sock socket. fdpass hands clamd an already-open file
# descriptor, because clamd runs as another user in another container and cannot
# open the worker's files itself.
#
# The rest keeps scanning failing closed (see Scholar::ClambyVirusScanner): check
# verifies clamdscan runs before each scan, without which a missing binary is
# reported as a virus; the error_* options raise instead of returning a verdict.
Clamby.configure(
  daemonize: true,
  fdpass: true,
  check: true,
  error_clamscan_missing: true,
  error_clamscan_client_error: true,
  error_file_missing: true,
  output_level: 'low'
)
