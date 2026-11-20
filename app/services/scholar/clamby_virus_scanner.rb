# frozen_string_literal: true

module Scholar
  # Scans files through Clamby (clamdscan --fdpass) against the clamd sidecar.
  # Hyrax's own VirusScanner only knows the abandoned ClamAV gem, so this is set
  # as Hyrax.config.virus_scanner in production (see config/initializers/hyrax.rb).
  #
  # A file that could not be scanned raises instead of being reported clean, so the
  # ingest job fails and Sidekiq retries it.
  class ClambyVirusScanner < Hyrax::VirusScanner
    class ScanError < StandardError; end

    def infected?
      result = Clamby.virus?(file)
      raise ScanError, "Unable to scan #{file} for viruses" if result.nil?

      warning("A virus was found in #{file}") if result
      result
    end
  end
end
