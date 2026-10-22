# frozen_string_literal: true

Bulkrax.setup do |config|
  # Add local parsers
  # config.parsers += [
  #   { name: 'Scholar', class_name: 'Bulkrax::ScholarParser', partial: 'csv_fields' }
  # ]
  config.parsers -= [
    { name: 'OAI - Dublin Core', class_name: 'Bulkrax::OaiDcParser', partial: 'oai_fields' },
    { name: 'OAI - Qualified Dublin Core', class_name: 'Bulkrax::OaiQualifiedDcParser', partial: 'oai_fields' },
    { name: 'Bagit', class_name: 'Bulkrax::BagitParser', partial: 'bagit_fields' },
    { name: 'XML', class_name: 'Bulkrax::XmlParser', partial: 'xml_fields' }
  ]

  # WorkType to use as the default if none is specified in the import
  # Default is the first returned by Hyrax.config.curation_concerns, stringified
  # Set explicitly: ProQuest ETD rows (app/services/proquest_bulkrax_csv.rb) have
  # no model column and rely on this.
  config.default_work_type = 'GraduateThesisOrDissertation'

  # Factory Class to use when generating and saving objects
  config.object_factory = Bulkrax::ObjectFactory
  # Use this for a Postgres-backed Valkyrized Hyrax
  # config.object_factory = Bulkrax::ValkyrieObjectFactory

  # Path to store pending imports
  config.import_path = '/data/tmp/csv_uploads'

  # Path to store exports before download
  config.export_path = '/data/tmp/import_files:shared'

  # Server name for oai request header
  # config.server_name = 'my_server@name.com'

  # NOTE: Creating Collections using the collection_field_mapping will no longer be supported as of Bulkrax version 3.0.
  #       Please configure Bulkrax to use related_parents_field_mapping and related_children_field_mapping instead.
  # Field_mapping for establishing a collection relationship (FROM work TO collection)
  # This value IS NOT used for OAI, so setting the OAI parser here will have no effect
  # The mapping is supplied per Entry, provide the full class name as a string, eg. 'Bulkrax::CsvEntry'
  # The default value for CSV is collection
  # Add/replace parsers, for example:
  # config.collection_field_mapping['Bulkrax::RdfEntry'] = 'http://opaquenamespace.org/ns/set'

  # Columns written by etd-loader/etd_loader.rb. Only its columns named
  # differently from the work property, or holding several values joined with
  # '|~|', need a mapping; every other column already matches a property.
  # - The property name comes first in `from` because CSV exports name each
  #   column after the first `from` entry.
  # - A mapping replaces Bulkrax's default one for that column, so `parsed: true`
  #   keeps the default clean-up of resource_type (matched to the authority)
  #   and subject (capitalised) for every CSV import.
  # - file: the loader's 'files' column; its split pattern is Bulkrax's default
  #   file separators ':', ';' and '|' plus the loader's '|~|'. Bulkrax checks
  #   files up front only in the first `from` column, so a missing file in a
  #   'files' column fails that entry rather than the whole import.
  etd_loader_split = '\s*\|~\|\s*'
  config.field_mappings['Bulkrax::CsvParser'] = {
    'parents' => { from: ['parents'], related_parents_field_mapping: true, split: true, join: true },
    'children' => { from: ['children'], related_children_field_mapping: true, split: true },
    'resource_type' => { from: ['resource_type'], parsed: true },
    'rights_statement' => { from: ['rights_statement'] },
    'creator' => { from: ['creator'], split: etd_loader_split },
    'keyword' => { from: ['keyword'], split: etd_loader_split },
    'subject' => { from: ['subject'], split: etd_loader_split, parsed: true },
    'contributor_advisor' => { from: ['contributor_advisor'], split: etd_loader_split },
    'contributor_committeemember' => { from: ['contributor_committeemember'], split: etd_loader_split },
    'file' => { from: %w[file files], split: '\s*(?:\|~\||[:;|])\s*' }
  }

  # Field mappings
  # Create a completely new set of mappings by replacing the whole set as follows
  #   config.field_mappings = {
  #     "Bulkrax::OaiDcParser" => { **individual field mappings go here*** }
  #   }

  # Add to, or change existing mappings as follows
  #   e.g. to exclude date
  #   config.field_mappings["Bulkrax::OaiDcParser"]["date"] = { from: ["date"], excluded: true  }
  #
  #   e.g. to import parent-child relationships
  #   config.field_mappings['Bulkrax::CsvParser']['parents'] = { from: ['parents'], related_parents_field_mapping: true }
  #   config.field_mappings['Bulkrax::CsvParser']['children'] = { from: ['children'], related_children_field_mapping: true }
  #   (For more info on importing relationships, see Bulkrax Wiki: https://github.com/samvera-labs/bulkrax/wiki/Configuring-Bulkrax#parent-child-relationship-field-mappings)
  #
  # #   e.g. to add the required source_identifier field
  #   #   config.field_mappings["Bulkrax::CsvParser"]["source_id"] = { from: ["old_source_id"], source_identifier: true, search_field: 'source_id_sim' }
  # If you want Bulkrax to fill in source_identifiers for you, see below

  # To duplicate a set of mappings from one parser to another
  #   config.field_mappings["Bulkrax::OaiOmekaParser"] = {}
  #   config.field_mappings["Bulkrax::OaiDcParser"].each {|key,value| config.field_mappings["Bulkrax::OaiOmekaParser"][key] = value }

  # Should Bulkrax make up source identifiers for you? This allow round tripping
  # and download errored entries to still work, but does mean if you upload the
  # same source record in two different files you WILL get duplicates.
  # It is given two aruguments, self at the time of call and the index of the reocrd
  #    config.fill_in_blank_source_identifiers = ->(parser, index) { "b-#{parser.importer.id}-#{index}"}
  # or use a uuid
  #    config.fill_in_blank_source_identifiers = ->(parser, index) { SecureRandom.uuid }
  config.fill_in_blank_source_identifiers = ->(obj, index) { "#{obj.importerexporter.id}-#{index}" }

  # Properties that should not be used in imports/exports. They are reserved for use by Hyrax.
  # config.reserved_properties += ['my_field']

  # List of Questioning Authority properties that are controlled via YAML files in
  # the config/authorities/ directory. For example, the :rights_statement property
  # is controlled by the active terms in config/authorities/rights_statements.yml
  # Defaults: 'rights_statement' and 'license'
  # config.qa_controlled_properties += ['my_field']

  # Specify the delimiter regular expression for splitting an attribute's values into a multi-value array.
  # config.multi_value_element_split_on = /\s*[:;|]\s*/.freeze

  # Specify the delimiter for joining an attribute's multi-value array into a string.  Note: the
  # specific delimeter should likely be present in the multi_value_element_split_on expression.
  # config.multi_value_element_join_on = ' | '
end

Bulkrax::CsvEntry.class_eval do
  # changing call from member_work_ids to member_ids as the former does not return results
  # removing in_work_ids as redundant, member_of_work_ids works for both works and filesets
  # removing file_set_ids as redundant, member_ids returns both child works and filesets
  def build_relationship_metadata
    # Includes all relationship methods for all exportable record types (works, Collections, FileSets)
    relationship_methods = {
      related_parents_parsed_mapping => %i[member_of_collection_ids member_of_work_ids],
      related_children_parsed_mapping => %i[member_collection_ids member_ids]
    }

    relationship_methods.each do |relationship_key, methods|
      next if relationship_key.blank?

      values = []
      methods.each do |m|
        values << hyrax_record.public_send(m) if hyrax_record.respond_to?(m)
      end
      values = values.flatten.uniq
      next if values.blank?

      handle_join_on_export(relationship_key, values, mapping[related_parents_parsed_mapping]['join'].present?)
    end
  end
end

Bulkrax::ObjectFactory.class_eval do
  def self.solr_name(field_name)
    if defined?(Hyrax) && Hyrax.respond_to?(:index_field_mapper)
      Hyrax.index_field_mapper.solr_name(field_name)
    else
      ActiveFedora.index_field_mapper.solr_name(field_name)
    end
  end
end

Bulkrax::ParserExportRecordSet::Base.class_eval do
  private

  # An export with no limit makes Bulkrax ask Solr for 2_147_483_647 rows.
  # SolrCloud sizes the queue that merges its shards' results by rows, so that
  # fails with java.lang.NegativeArraySizeException. No query can match more
  # documents than the index holds, so ask for that many instead.
  def row_limit
    return limit unless limit.zero?

    @row_limit ||= [ActiveFedora::SolrService.count('*:*'), 1].max
  end
end

# Sidebar for hyrax 3+ support
if Object.const_defined?(:Hyrax) && ::Hyrax::DashboardController.respond_to?(:sidebar_partials)
  Hyrax::DashboardController.sidebar_partials[:repository_content] << 'hyrax/dashboard/sidebar/bulkrax_sidebar_additions'
end
