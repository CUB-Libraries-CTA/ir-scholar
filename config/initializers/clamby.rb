# frozen_string_literal: true

# clamdscan talks to clamd in the worker's sidecar container over the shared
# /run/clamav/clamd.sock socket. fdpass hands clamd an already-open file
# descriptor, because clamd runs as another user in another container and cannot
# open the worker's files itself. error_clamscan_client_error raises when clamd
# can't be reached, instead of Clamby's default of reporting the file as infected.
Clamby.configure(
  daemonize: true,
  fdpass: true,
  error_clamscan_client_error: true,
  output_level: 'low'
)
