# frozen_string_literal: true

## CUBL Change:
# Force this controller to use only ActiveFedora resources because of the
# compatibility issues in Hyrax v3.x between existing ActiveFedora data and
# Wings/Valkyrie code
Hyrax::FileSetsController.class_eval do
  before_action :cast_file_set

  # This casts FileSets to their ActiveFedora resources before controller actions
  def cast_file_set
    return unless @file_set.instance_of?(::FileSet)

    @file_set = ActiveFedora::Base.find(@file_set.id.to_s)
  end

  def parent(file_set: curation_concern)
    # Return the parent if it has already been found/set
    if file_set.respond_to?(:parent) && !file_set.parent.nil?
      return @parent ||= file_set.parent
    end

    @parent ||=
      case file_set
      when Hyrax::Resource
        # Handle Wings/Valkyrie FileSets
        Hyrax.query_service.find_parents(resource: file_set).first
      else
        # ids = Hyrax::SolrService.query("{!field f=member_ids_ssim}#{file_set.id}",
        # Retrieve the parent ID(s) of the ActiveFedora FileSet
        ids = ActiveFedora::SolrService.query("{!field f=member_ids_ssim}#{file_set.id}",
                                              fl: ActiveFedora.id_field)
                                       .map { |x| x.fetch(ActiveFedora.id_field) }
        Hyrax.logger.warn("Couldn't find a parent work for FileSet: #{file_set.id}.") if ids.empty?

        ActiveFedora::Base.find(ids.first.to_s)
      end
  end
end
