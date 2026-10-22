# frozen_string_literal: true

require 'aws-sdk-s3'
require 'csv'
require 'fileutils'
require 'logger'
require 'nokogiri'
require 'zip'

# Loads ProQuest ETD packages into Scholar.
#
# Each ProQuest zip holds a DISS_submission XML record plus the PDF and any
# attachments. For each zip in SOURCE_DIR (at most LIMIT of them; a negative
# LIMIT means all), the loader:
#   1. extracts it into DEST_DIR/<zip name>
#   2. turns each accepted XML record into a row of an import CSV, uploading
#      the XML to S3 along the way
#   3. runs `rake etd_import:upload[<csv>]` from the Scholar app root
#   4. renames the zip to *.zip.processed, or moves it into SOURCE_DIR/rejected
#      or SOURCE_DIR/unaccepted
#
# Usage: bundle exec ruby etd_loader.rb SOURCE_DIR DEST_DIR LIMIT
module EtdLoader
  APP_ROOT = '/data'
  LOG_DIR = '/efs/test/proquest/logs'
  CSV_DIVIDER = '|~|'
  USAGE = 'Usage: ruby etd_loader.rb SOURCE_DIR DEST_DIR LIMIT'

  class RakeTaskError < StandardError; end

  module_function

  def build_logger(log_dir = LOG_DIR)
    logger = Logger.new(File.join(log_dir, "etd-loader-#{Time.now.strftime('%Y-%m-%d_%H:%M:%S')}.log"))
    logger.formatter = proc do |severity, time, _progname, msg|
      format("%<time>s,%<ms>03d %<severity>s %<msg>s\n",
             time: time.strftime('%Y-%m-%d %H:%M:%S'), ms: time.usec / 1000, severity: severity, msg: msg)
    end
    logger.level = Logger::DEBUG
    logger
  end

  # Whether ProQuest marked the parsed DISS_submission as accepted.
  def accepted?(submission)
    acceptance = submission.fetch('DISS_repository').fetch('DISS_acceptance', nil)
    !acceptance.nil? && acceptance != '0'
  end

  # Like `mv`: moves into `destination` if it is a directory, otherwise renames.
  # A failed move is reported and processing carries on.
  def move(source, destination)
    FileUtils.mv(source, destination)
  rescue SystemCallError => e
    warn "mv: #{e.message}"
  end

  # Extracts every entry under `destination`, overwriting existing files and
  # dropping '', '.' and '..' path components so nothing escapes it.
  def extract_all(zip, destination)
    zip.each do |entry|
      relative_path = entry.name.split('/').reject { |part| ['', '.', '..'].include?(part) }.join('/')
      next if relative_path.empty?

      target = File.join(destination, relative_path)
      if entry.directory?
        FileUtils.mkdir_p(target)
      else
        FileUtils.mkdir_p(File.dirname(target))
        entry.extract(target) { true }
      end
    end
  end

  # Extracts every zip in `source_dir` into its own new directory under
  # `destination_dir`, printing each zip's contents first.
  def unzip_all(source_dir, destination_dir)
    Dir.glob('*.zip', base: source_dir).each do |zip_name|
      target = File.join(destination_dir, File.basename(zip_name, '.*'))
      Dir.mkdir(target)
      Zip::File.open(File.join(source_dir, zip_name)) do |zip|
        print_listing(zip)
        extract_all(zip, target)
      end
    end
  end

  def print_listing(zip)
    puts format('%<name>-46s %<modified>19s %<size>12s', name: 'File Name', modified: 'Modified    ', size: 'Size')
    zip.each do |entry|
      puts format('%<name>-46s %<modified>s %<size>12d',
                  name: entry.name, modified: entry.time.strftime('%Y-%m-%d %H:%M:%S'), size: entry.size)
    end
  end

  # Prints the title of every XML record in the given processing-folder packages.
  def print_titles(packages, root: '/efs/prod/proquest/processing_folder')
    packages.each do |package|
      Dir.glob(File.join(root, package, '*.xml')).each do |path|
        puts XmlHash.parse(File.binread(path))
                    .fetch('DISS_submission').fetch('DISS_description').fetch('DISS_title')
      end
    end
  end

  # Converts XML into nested Hashes, Arrays and Strings:
  #   - attributes become '@name' keys, namespaced names become '<uri>:<name>'
  #   - text next to attributes or child elements goes under '#text'
  #   - an element holding only text becomes a String; an empty one becomes nil
  #   - repeated sibling elements become an Array
  #   - text is whitespace-stripped
  module XmlHash
    module_function

    # Pass raw bytes (File.binread): the XML declaration decides the encoding,
    # and undeclared input that isn't valid UTF-8 is read as ISO-8859-1.
    def parse(xml)
      root = parse_document(xml).root
      { qualified_name(root) => convert(root) }
    end

    def parse_document(xml)
      strict_parse(xml, nil)
    rescue Nokogiri::XML::SyntaxError
      raise if xml.dup.force_encoding(Encoding::UTF_8).valid_encoding?

      strict_parse(xml, 'ISO-8859-1')
    end

    # Malformed XML raises instead of being silently recovered.
    def strict_parse(xml, encoding)
      Nokogiri::XML(xml, nil, encoding) { |config| config.strict.nonet }
    end

    def convert(node)
      result = {}
      node.attribute_nodes.each { |attr| result["@#{qualified_name(attr)}"] = attr.value }
      text = +''
      node.children.each do |child|
        if child.element?
          append(result, qualified_name(child), convert(child))
        elsif child.text? || child.cdata?
          text << child.content
        end
      end
      text = text.strip
      return (text.empty? ? nil : text) if result.empty?

      result['#text'] = text unless text.empty?
      result
    end

    def append(hash, key, value)
      if hash.key?(key)
        hash[key] = [hash[key]] unless hash[key].is_a?(Array)
        hash[key] << value
      else
        hash[key] = value
      end
    end

    def qualified_name(node)
      node.namespace ? "#{node.namespace.href}:#{node.name}" : node.name
    end
  end

  # Uploads XML records to the public metadata area of the static bucket.
  class S3Uploader
    BUCKET = 'cubl-static'
    KEY_PREFIX = 'samvera/metadata/test'
    REGION = 'us-west-2'

    # A failed upload is fatal for the record, unlike a failed bucket lookup.
    class UploadFailedError < StandardError; end

    def initialize(client: nil, error_logger: nil)
      @client = client
      @error_logger = error_logger || Logger.new($stderr, formatter: proc { |sev, _, _, msg| "#{sev}:root:#{msg}\n" })
    end

    # Returns the object's public URL, or false if the bucket lookup fails.
    def upload(path)
      file_name = File.basename(path)
      key = "#{KEY_PREFIX}/#{file_name}"
      puts "- Upload XML file to S3: #{file_name}"
      upload_file(path, key)
      location = client.get_bucket_location(bucket: BUCKET).location_constraint
      "https://s3-#{location}.amazonaws.com/#{BUCKET}/#{key}"
    rescue Aws::S3::Errors::ServiceError => e
      @error_logger.error(e)
      false
    end

    private

    def client
      @client ||= Aws::S3::Client.new({ region: REGION }.merge(app_credentials))
    end

    # The worker provides its key and secret as AWS_S3_APP_KEY and
    # AWS_S3_APP_SECRET (browse-everything's names), which the AWS SDK doesn't
    # read itself. Without both, the SDK's usual credential lookup applies.
    def app_credentials
      key, secret = ENV.values_at('AWS_S3_APP_KEY', 'AWS_S3_APP_SECRET')
      return {} if key.to_s.empty? || secret.to_s.empty?

      { credentials: Aws::Credentials.new(key, secret) }
    end

    def upload_file(path, key)
      Aws::S3::TransferManager.new(client: client).upload_file(path, bucket: BUCKET, key: key)
    rescue Aws::S3::Errors::ServiceError, Aws::S3::MultipartUploadError => e
      raise UploadFailedError, "Failed to upload #{path} to #{BUCKET}/#{key}: #{e.message}"
    end
  end

  # Turns a parsed DISS_submission into one import CSV row. Missing required
  # elements raise (KeyError, IndexError, ...) so the package gets rejected.
  class RecordBuilder
    HEADERS = ['title', 'resource type', 'creator', 'contributor', 'keyword', 'license', 'rights statement',
               'publisher', 'subject', 'language', 'location', 'bibliographic_citation', 'source', 'abstract',
               'academic_affiliation', 'additional_information', 'alternative_title', 'contributor_advisor',
               'contributor_committeemember', 'date_issued', 'degree_grantors', 'degree_level', 'doi',
               'embargo_reason', 'graduation_year', 'peerreviewed', 'degree_name', 'admin_set_id', 'visibility',
               'files', 'replaces', 'embargo_release_date'].freeze

    DEFAULTS = {
      'language' => 'http://id.loc.gov/vocabulary/iso639-2/eng',
      'rights statement' => 'http://rightsstatements.org/vocab/InC/1.0/',
      'degree_grantors' => 'http://id.loc.gov/authorities/names/n50000485',
      'admin_set_id' => 'k643b116n',
      'visibility' => 'open',
      'resource type' => 'Dissertation',
      'degree_level' => 'Doctoral',
      'publisher' => 'University of Colorado Boulder'
    }.freeze

    ACADEMIC_AFFILIATION_MAP = File.expand_path('academicAffiliationMap.csv', __dir__)

    def initialize(uploader:)
      @uploader = uploader
    end

    # `package_dir` is where the zip was extracted; `folder_name` is the
    # package's directory name, used as the prefix of every file path.
    def build(submission, file_number:, package_dir:, xml_name:, folder_name:)
      description = submission.fetch('DISS_description')
      content = submission.fetch('DISS_content')
      restriction = submission.fetch('DISS_restriction')
      repository = submission.fetch('DISS_repository')
      categorization = description.fetch('DISS_categorization')
      masters = description.fetch('@type') == 'masters'
      embargo_code = submission.fetch('@embargo_code')
      keywords = keywords(categorization)
      date_issued = date_issued(repository.fetch('DISS_agreement_decision_date', nil),
                                description.fetch('DISS_dates', nil))
      release_date = embargo_release_date(embargo_code, restriction)

      HEADERS.to_h { |header| [header, ''] }.merge!(DEFAULTS).merge!(
        'title' => description.fetch('DISS_title', 'No Title'),
        'resource type' => masters ? 'Masters Thesis' : 'Dissertation',
        'creator' => names(submission.fetch('DISS_authorship').fetch('DISS_author')),
        'keyword' => keywords,
        # `+` (not interpolation) so a nil subject list raises
        'subject' => keywords + CSV_DIVIDER + subjects(categorization.fetch('DISS_category')),
        'abstract' => abstract(content.fetch('DISS_abstract').fetch('DISS_para', 'No Abstract')),
        'academic_affiliation' => description.fetch('DISS_institution').fetch('DISS_inst_contact', ''),
        'contributor_advisor' => optional_names(description, 'DISS_advisor'),
        'contributor_committeemember' => optional_names(description, 'DISS_cmte_member'),
        'files' => files(content, package_dir, folder_name),
        'date_issued' => date_issued,
        'embargo_release_date' => release_date,
        'visibility' => visibility(embargo_code, release_date),
        'degree_level' => masters ? "Master's" : 'Doctoral',
        'graduation_year' => date_issued.partition('-').first,
        'degree_name' => description.fetch('DISS_degree'),
        'replaces' => "#{file_number}|#{@uploader.upload(File.join(package_dir, xml_name))}"
      )
    end

    # Maps a bepress department URL to its Scholar academic affiliation.
    def academic_affiliation(front_end_url)
      department = front_end_url.split('/', -1).fetch(-2)
      # Leading spaces are ignored; trailing ones are kept
      lstrip = ->(field) { field&.lstrip }
      row = CSV.foreach(ACADEMIC_AFFILIATION_MAP, col_sep: '|', headers: true,
                        header_converters: lstrip, converters: lstrip)
               .find { |r| r.fetch('bepress') == department }
      row ? row.fetch('samvera') : 'Other'
    end

    private

    def keywords(categorization)
      keyword = categorization.fetch('DISS_keyword', nil)
      keyword.nil? ? '' : keyword.gsub(', ', CSV_DIVIDER)
    end

    def subjects(category)
      return category.fetch('DISS_cat_desc') unless category.is_a?(Array)

      category.map { |cat| cat.fetch('DISS_cat_desc') }.compact.join(CSV_DIVIDER)
    end

    def abstract(paragraphs)
      paragraphs.is_a?(Array) ? paragraphs.join : paragraphs
    end

    def optional_names(description, key)
      description.key?(key) ? names(description[key]) : ''
    end

    def names(people)
      list(people).map { |person| format_name(person.fetch('DISS_name')) }.join(CSV_DIVIDER)
    end

    def format_name(name)
      surname, first, middle, suffix = %w[DISS_surname DISS_fname DISS_middle DISS_suffix].map do |key|
        name.fetch(key, nil).to_s
      end
      full_name = "#{surname}, #{first} #{middle}"
      suffix.empty? ? full_name : "#{full_name}, #{suffix}"
    end

    # The main PDF, then any attachments, which sit in the package's subfolder.
    def files(content, package_dir, folder_name)
      paths = [File.join(folder_name, content.fetch('DISS_binary').fetch('#text'))]
      if content.key?('DISS_attachment')
        subfolder = Dir.children(package_dir).select { |entry| File.directory?(File.join(package_dir, entry)) }.fetch(0)
        list(content['DISS_attachment']).each do |attachment|
          paths << File.join(folder_name, subfolder, attachment.fetch('DISS_file_name'))
        end
      end
      paths.join(CSV_DIVIDER)
    end

    # The agreement decision date's day; otherwise the accept date (MM/DD/YYYY)
    # as YYYY-MM-DD, or just YYYY when it is 01/01.
    def date_issued(decision_date, dates)
      return decision_date.partition(' ').first unless decision_date.nil?

      accept_date = dates.fetch('DISS_accept_date', nil)
      return if accept_date.nil?

      month, day, year = month_day_year(accept_date)
      month == '01' && day == '01' ? year : "#{year}-#{month}-#{day}"
    end

    def embargo_release_date(embargo_code, restriction)
      return '' if embargo_code == '0' || restriction.nil?

      month, day, year = month_day_year(restriction.fetch('DISS_sales_restriction', nil).fetch('@remove', nil))
      "#{year}-#{month}-#{day}"
    end

    def visibility(embargo_code, release_date)
      embargo_code != '0' && release_date > Time.now.strftime('%Y-%m-%d') ? 'embargo' : 'open'
    end

    def month_day_year(date)
      parts = date.split('/', -1)
      [parts.fetch(0), parts.fetch(1), parts.fetch(2)]
    end

    def list(value)
      value.is_a?(Array) ? value : [value]
    end
  end

  # Converts the XML records of one extracted package into an import CSV, runs
  # the import, and files the zip as processed, rejected or unaccepted.
  class PackageProcessor
    CSV_NAME = 'dissertation_dataload.csv'

    def initialize(logger:, record_builder:, app_root: APP_ROOT)
      @logger = logger
      @record_builder = record_builder
      @app_root = app_root
    end

    def process(package_dir:, file_number:, folder_name:, zip_path:, rejected_dir:, unaccepted_dir:)
      rows = []
      Dir.children(package_dir).select { |name| name.end_with?('.xml') }.each do |xml_name|
        puts "- Found: #{xml_name}"
        submission = XmlHash.parse(File.binread(File.join(package_dir, xml_name))).fetch('DISS_submission')
        unless EtdLoader.accepted?(submission)
          reject_unaccepted(zip_path, unaccepted_dir)
          next
        end

        rows << @record_builder.build(submission, file_number: file_number, package_dir: package_dir,
                                      xml_name: xml_name, folder_name: folder_name)
        import(write_csv(package_dir, rows))
        EtdLoader.move(zip_path, "#{zip_path}.processed")
        puts '- Converted .zip to .zip.processed', '- Done'
        @logger.debug("Success: #{folder_name}")
      end
    rescue StandardError => e
      puts "- Error Found:#{e}"
      @logger.error("- Log: #{zip_path} : #{e}")
      EtdLoader.move(zip_path, rejected_dir)
      puts '- Moved to Rejected folder'
    end

    private

    def reject_unaccepted(zip_path, unaccepted_dir)
      puts "- Log: #{zip_path}:is not accepted"
      @logger.error("#{zip_path} : is not accepted")
      EtdLoader.move(zip_path, unaccepted_dir)
      puts '- Moved to UNACCEPTED folder'
    end

    # Rewrites the CSV with every row so far; returns its path.
    def write_csv(package_dir, rows)
      path = File.join(package_dir, CSV_NAME)
      headers = rows.first.keys
      CSV.open(path, 'w', row_sep: "\r\n", quote_empty: false) do |csv|
        csv << headers
        rows.each { |row| csv << row.values_at(*headers) }
      end
      puts "- Converted to : #{path}"
      path
    end

    def import(csv_path)
      task = "etd_import:upload[#{csv_path}]"
      puts "- Execute Rake Task: rake #{task} --trace"
      return if system('rake', task, '--trace', chdir: @app_root)

      raise RakeTaskError, "rake #{task} failed"
    end
  end

  # Runs the loader over the zips in a source directory.
  class Runner
    def self.from_argv(argv)
      abort USAGE if argv.size < 3

      new(source_dir: File.expand_path(argv[0]), destination_dir: File.expand_path(argv[1]),
          limit: Integer(argv[2], 10))
    end

    def initialize(source_dir:, destination_dir:, limit:, processor: nil)
      @source_dir = source_dir
      @destination_dir = destination_dir
      @limit = limit
      @processor = processor || PackageProcessor.new(
        logger: EtdLoader.build_logger, record_builder: RecordBuilder.new(uploader: S3Uploader.new)
      )
    end

    def run
      zip_names = Dir.glob('*.zip', base: @source_dir)
      zip_names = zip_names.first(@limit) unless @limit.negative?
      zip_names.each.with_index(1) { |zip_name, count| process_zip(zip_name, count) }
    end

    private

    def process_zip(zip_name, count)
      folder_name = File.basename(zip_name, '.*')
      zip_path = File.join(@source_dir, zip_name)
      package_dir = File.join(@destination_dir, folder_name)
      FileUtils.mkdir_p(package_dir)
      return unless extract(zip_path, package_dir, folder_name, count)

      @processor.process(package_dir: package_dir, file_number: folder_name.rpartition('_').last,
                         folder_name: folder_name, zip_path: zip_path,
                         rejected_dir: File.join(@source_dir, 'rejected'),
                         unaccepted_dir: File.join(@source_dir, 'unaccepted'))
    end

    def extract(zip_path, package_dir, folder_name, count)
      Zip::File.open(zip_path) do |zip|
        puts "#{count}: #{folder_name}", "- Unzipping: #{folder_name}"
        EtdLoader.extract_all(zip, package_dir)
      end
      true
    rescue Zip::Error
      puts "Bad Zipfile: #{folder_name}"
      EtdLoader.move(zip_path, File.join(@source_dir, 'rejected'))
      puts '- Moved to Rejected folder'
      false
    end
  end
end

EtdLoader::Runner.from_argv(ARGV).run if $PROGRAM_NAME == __FILE__
