# frozen_string_literal: true

## CUBL Change:
# ProQuest ETD packages (a zip of DISS_submission XML, PDF and attachments)
# have no import CSV. After unzipping one, build the CSV that Bulkrax's
# CsvParser expects so the package imports like any other CSV zip.
Bulkrax::ImporterJob.class_eval do
  private

  def unzip_imported_file(parser)
    return unless parser.file? && parser.zip?

    parser.unzip(parser.parser_fields['import_file_path'])
    write_proquest_csv(parser) if parser.is_a?(Bulkrax::CsvParser)
    parser.remove_spaces_from_filenames
  end

  # Records the failure on the importer so it shows in the Bulkrax UI, then
  # re-raises to stop the job before Bulkrax looks for a CSV.
  def write_proquest_csv(parser)
    proquest_csv = ProquestBulkraxCsv.new(unzip_path: parser.importer_unzip_path,
                                          import_file_path: parser.parser_fields['import_file_path'])
    proquest_csv.write if proquest_csv.proquest_package?
  rescue StandardError => e
    parser.set_status_info(e)
    raise
  end
end

## CUBL Change:
# Let files/ in an import zip have subfolders. Bulkrax 9.1.0 only treats a CSV
# as an attachment when its own folder is named 'files', so a CSV in
# files/<subfolder>/ could be imported as the import CSV.
Bulkrax::CsvParser.class_eval do
  private

  def in_files_dir?(path)
    File.dirname(path).ends_with?('files') || path.start_with?("#{File.join(importer_unzip_path, 'files')}/")
  end
end

## CUBL Change:
# CsvEntry#add_file replaces spaces with underscores in the whole relative
# path, folder names included, but Bulkrax 9.1.0 only renames the entries
# directly inside files/. Rename at every depth, deepest first so renaming a
# folder doesn't move paths still waiting to be renamed.
Bulkrax::ApplicationParser.class_eval do
  def remove_spaces_from_filenames
    paths = Dir.glob(File.join(importer_unzip_path, 'files', '**', '* *'))
    paths.sort_by { |path| -path.count('/') }.each do |path|
      File.rename(path, File.join(File.dirname(path), File.basename(path).tr(' ', '_')))
    end
  end
end
