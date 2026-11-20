# frozen_string_literal: true

require 'rails_helper'

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
