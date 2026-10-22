# frozen_string_literal: true

require Rails.root.join('etd-loader', 'etd_loader').to_s

# Builds the CSV that Bulkrax's CsvParser imports from an unzipped ProQuest
# ETD package. Each accepted *_DATA.xml record becomes one row, and the whole
# package is then moved into
# files/, folders intact, where Bulkrax looks for the files to attach. Bulkrax
# needs the files/ patches in config/initializers/hacks/importer_job.rb to
# handle subfolders there.
#
# The metadata comes from EtdLoader::RecordBuilder (etd-loader/etd_loader.rb),
# so ETDs imported through Bulkrax get the same values as the standalone loader.
class ProquestBulkraxCsv
  CSV_NAME = 'proquest_import.csv'
  MODEL = 'GraduateThesisOrDissertation'

  # etd_loader column => work property. Left out: the columns the loader always
  # leaves blank, 'source' (Bulkrax stores the source identifier there) and
  # 'admin_set_id' (the importer supplies the admin set).
  PROPERTIES = {
    'title' => 'title',
    'resource type' => 'resource_type',
    'creator' => 'creator',
    'keyword' => 'keyword',
    'rights statement' => 'rights_statement',
    'publisher' => 'publisher',
    'subject' => 'subject',
    'language' => 'language',
    'abstract' => 'abstract',
    'academic_affiliation' => 'academic_affiliation',
    'contributor_advisor' => 'contributor_advisor',
    'contributor_committeemember' => 'contributor_committeemember',
    'date_issued' => 'date_issued',
    'degree_grantors' => 'degree_grantors',
    'degree_level' => 'degree_level',
    'graduation_year' => 'graduation_year',
    'degree_name' => 'degree_name',
    'replaces' => 'replaces',
    'visibility' => 'visibility',
    'embargo_release_date' => 'embargo_release_date'
  }.freeze

  # Properties the loader joins with EtdLoader::CSV_DIVIDER. They are written as
  # numbered columns (creator_1, creator_2, ...), which Bulkrax reads as one
  # multi-value field without splitting values on its own delimiters.
  MULTI_VALUED = %w[creator keyword subject contributor_advisor contributor_committeemember].freeze

  # Values for columns that would otherwise be empty: etd_loader's DEFAULTS,
  # keyed by work property. Its admin_set_id is left out because Bulkrax would
  # use it instead of the admin set chosen on the importer.
  DEFAULTS = EtdLoader::RecordBuilder::DEFAULTS.except('admin_set_id')
                                               .transform_keys { |column| PROPERTIES.fetch(column, column) }.freeze

  class Error < StandardError; end

  def initialize(unzip_path:, import_file_path:, record_builder: nil)
    @unzip_path = unzip_path
    @files_dir = File.join(unzip_path, 'files')
    # Same file number the loader took from the zip name, e.g. etdadmin_upload_123456 -> 123456
    @file_number = File.basename(import_file_path, '.*').rpartition('_').last
    @record_builder = record_builder ||
                      EtdLoader::RecordBuilder.new(uploader: EtdLoader::S3Uploader.new(error_logger: Rails.logger))
  end

  # ProQuest packages are recognised by their *_DATA.xml records. Their
  # attachments may themselves be CSVs, so the presence of a CSV says nothing.
  def proquest_package?
    xml_paths.any?
  end

  # Builds the rows while the package is still laid out as unzipped, moves the
  # package into files/, then writes the import CSV into the unzip folder and
  # returns its path. Safe to run again on a retried job over the same folder.
  def write
    rows = xml_paths.map { |path| row_for(path) }.compact
    raise Error, "No accepted ProQuest records in #{@unzip_path}" if rows.empty?

    move_package_into_files_dir
    check_files_present(rows)
    write_csv(rows)
  end

  private

  def row_for(xml_path)
    submission = EtdLoader::XmlHash.parse(File.binread(xml_path)).fetch('DISS_submission')
    unless EtdLoader.accepted?(submission)
      Rails.logger.warn("ProQuest record #{xml_path} is not accepted; skipping it")
      return
    end

    xml_dir = File.dirname(xml_path)
    record = @record_builder.build(submission, file_number: @file_number, package_dir: xml_dir,
                                   xml_name: File.basename(xml_path), folder_name: '.')
    file_names = record.fetch('files').split(EtdLoader::CSV_DIVIDER).map do |path|
      package_path(File.basename(path), xml_dir)
    end
    bulkrax_row(record, source_identifier: File.basename(xml_path, '.xml'), file_names: file_names)
  end

  # Where a file the record lists sits in the package, relative to the unzip
  # folder (and so to files/ once moved). Found by name in the XML's folder and
  # its subfolders: the loader's own path guesses the attachment folder as the
  # first subfolder it sees, which can be files/ or __MACOSX.
  def package_path(name, xml_dir)
    matches = package_files('*').select do |path|
      File.basename(path) == name && path.start_with?("#{xml_dir}/") && File.file?(path)
    end
    raise Error, "#{name} is listed in the ProQuest record but missing from the package" if matches.empty?
    raise Error, "The ProQuest package has several files named #{name}" if matches.size > 1

    matches.first.delete_prefix("#{@unzip_path}/")
  end

  def bulkrax_row(record, source_identifier:, file_names:)
    row = { 'source_identifier' => source_identifier, 'model' => MODEL }
    PROPERTIES.each do |column, property|
      value = record.fetch(column)
      if MULTI_VALUED.include?(property)
        value.split(EtdLoader::CSV_DIVIDER).reject(&:blank?).each.with_index(1) { |v, i| row["#{property}_#{i}"] = v }
      elsif value.present?
        row[property] = value
      end
    end
    DEFAULTS.each { |property, default| row[property] = default if row[property].blank? }
    embargo!(row)
    row['file'] = file_names.join('|')
    row
  end

  # Hyrax only applies an embargo when it is told what to show during and after it.
  def embargo!(row)
    if row['visibility'] == 'embargo'
      row['visibility_during_embargo'] = 'restricted'
      row['visibility_after_embargo'] = 'open'
    else
      row.delete('embargo_release_date')
    end
  end

  # Moves the whole package into files/, folders intact, leaving just files/
  # and the import CSV. Identical files already in files/ are fine, so a
  # retried job can run again after Bulkrax re-extracts the zip.
  def move_package_into_files_dir
    package_files('*').each do |source|
      next unless File.file?(source) && source != csv_path

      relative_path = source.delete_prefix("#{@unzip_path}/")
      target = File.join(@files_dir, relative_path)
      if !File.exist?(target)
        FileUtils.mkdir_p(File.dirname(target))
        FileUtils.mv(source, target)
      elsif FileUtils.compare_file(source, target)
        FileUtils.rm(source)
      else
        raise Error, "files/#{relative_path} already exists with different content"
      end
    end
    remove_leftover_dirs
  end

  # macOS zip metadata (its ._*.csv files would also count as import CSVs) and
  # the package's now-empty folders, deepest first.
  def remove_leftover_dirs
    FileUtils.rm_rf(File.join(@unzip_path, '__MACOSX'))
    dirs = Dir.glob(File.join(@unzip_path, '**', '*/')).map { |dir| dir.chomp('/') }
    dirs.sort_by { |dir| -dir.length }.each do |dir|
      next if dir == @files_dir || dir.start_with?("#{@files_dir}/")

      Dir.rmdir(dir) if Dir.empty?(dir)
    end
  end

  def check_files_present(rows)
    missing = rows.flat_map { |row| row['file'].split('|') }.reject { |name| File.file?(File.join(@files_dir, name)) }
    return if missing.empty?

    raise Error, "Listed in the ProQuest record but missing from the package: #{missing.uniq.join(', ')}"
  end

  def write_csv(rows)
    headers = rows.flat_map(&:keys).uniq
    CSV.open(csv_path, 'w') do |csv|
      csv << headers
      rows.each { |row| csv << row.values_at(*headers) }
    end
    csv_path
  end

  def csv_path
    File.join(@unzip_path, CSV_NAME)
  end

  def xml_paths
    package_files('*_DATA.xml')
  end

  # Matching files anywhere in the unzip folder except files/ and macOS zip metadata.
  def package_files(pattern)
    Dir.glob(File.join(@unzip_path, '**', pattern)).sort.reject do |path|
      path.start_with?("#{@files_dir}/") || path.split('/').include?('__MACOSX')
    end
  end
end
