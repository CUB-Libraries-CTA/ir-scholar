# frozen_string_literal: true

require 'rails_helper'

RSpec.describe Scholar::ClambyVirusScanner do
  let(:path) { '/tmp/upload.pdf' }

  it 'is a Hyrax virus scanner' do
    expect(described_class.new(path)).to be_a(Hyrax::VirusScanner)
  end

  it 'raises when Clamby could not scan (nil) instead of reporting clean' do
    allow(Clamby).to receive(:virus?).with(path).and_return(nil)
    expect { described_class.infected?(path) }.to raise_error(Scholar::ClambyVirusScanner::ScanError)
  end

  it 'lets Clamby errors (e.g. sidecar down) propagate so the job retries' do
    allow(Clamby).to receive(:virus?).and_raise(Clamby::ClamscanClientError)
    expect { described_class.infected?(path) }.to raise_error(Clamby::ClamscanClientError)
  end

  # Runs the real Clamby, with the app's initializer config, against a stand-in
  # clamdscan that answers --version and then exits with the given status on scan.
  context 'with the configured Clamby and a fake clamdscan' do
    let(:dir) { Dir.mktmpdir }
    let(:upload) { File.join(dir, 'upload.pdf').tap { |f| File.write(f, 'data') } }

    around do |example|
      original = Clamby.config[:executable_path_clamdscan]
      example.run
    ensure
      Clamby.config[:executable_path_clamdscan] = original
      FileUtils.remove_entry(dir)
    end

    def fake_clamdscan(scan_status)
      File.join(dir, 'clamdscan').tap do |script|
        File.write(script, "#!/bin/sh\ncase \"$*\" in *--version*) exit 0;; esac\nexit #{scan_status}\n")
        File.chmod(0o755, script)
        Clamby.config[:executable_path_clamdscan] = script
      end
    end

    it 'reports infected when clamdscan finds a virus (exit 1)' do
      fake_clamdscan(1)
      expect(described_class.infected?(upload)).to be true
    end

    it 'reports clean when clamdscan finds nothing (exit 0)' do
      fake_clamdscan(0)
      expect(described_class.infected?(upload)).to be false
    end

    it 'raises when clamdscan cannot reach clamd (exit 2)' do
      fake_clamdscan(2)
      expect { described_class.infected?(upload) }.to raise_error(Clamby::ClamscanClientError)
    end

    it 'raises instead of reporting infected when clamdscan fails some other way (exit 127)' do
      fake_clamdscan(127)
      expect { described_class.infected?(upload) }.to raise_error(Scholar::ClambyVirusScanner::ScanError)
    end

    it 'raises when clamdscan is missing' do
      Clamby.config[:executable_path_clamdscan] = File.join(dir, 'missing-clamdscan')
      expect { described_class.infected?(upload) }.to raise_error(Clamby::ClamscanMissing)
    end

    it 'raises when the upload is missing' do
      fake_clamdscan(0)
      expect { described_class.infected?(File.join(dir, 'gone.pdf')) }.to raise_error(Clamby::FileNotFound)
    end
  end
end
