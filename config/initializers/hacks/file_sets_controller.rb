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
    Hyrax.logger.warn "Initial file_set: #{@file_set}"
  end

  def parent(file_set: curation_concern)
    # Hyrax.logger.warn "setting parent for file_set: #{file_set.inspect}"
    # Return the parent if it has already been found/set
    if file_set.respond_to?(:parent) && !file_set.parent.nil?
      return @parent ||= file_set.parent
    end

    # v_file_set = file_set.valkyrie_resource if file_set.respond_to?(:parent) && file_set.parent.nil?
    # Hyrax.logger.warn "file_set: #{file_set.inspect}"
    @parent ||=
      case file_set
      when Hyrax::Resource
        # TODO: Check if this branch or the following branch needs to be removed; FileSets from outside code outside of
        # TODO: this controller might not be the ActiveFedora resource
        # Handle Wings/Valkyrie FileSets
        par = Hyrax.query_service.find_parents(resource: file_set).first
        # Hyrax.logger.warn "Hyrax file_set parent: #{par.inspect}"
        par
      when Hyrax::FileSetPresenter
        # TODO: Check if this branch is ever run
        # This handles the infrequent case of a presenter being referenced instead
        # of its FileSet
        Hyrax.logger.warn "file_set_presenter parent: #{file_set.parent.inspect}"
        file_set.parent
      else
        # ids = Hyrax::SolrService.query("{!field f=member_ids_ssim}#{file_set.id}",
        # Retrieve the parent ID(s) of the ActiveFedora FileSet
        ids = ActiveFedora::SolrService.query("{!field f=member_ids_ssim}#{file_set.id}",
                                              fl: ActiveFedora.id_field)
                                       .map { |x| x.fetch(ActiveFedora.id_field) }
        Hyrax.logger.warn("Couldn't find a parent work for FileSet: #{file_set.id}.") if ids.empty?
        # Hyrax.logger.warn "IDs: #{ids}"
        af_work = ActiveFedora::Base.find(ids.first.to_s)
        Hyrax.logger.warn "Parent work: #{af_work}"
        return af_work
        # par = Hyrax.query_service.find_parents(resource: file_set).first
        # Hyrax.logger.warn "file_set parent: #{par.inspect}"
        # par
      end
  end

  # private

  # def initialize_edit_form
  #   Hyrax.logger.warn "Initialize edit form for: #{file_set.inspect}"
  #   Hyrax.logger.warn "Parent: #{parent.inspect}"
  #   guard_for_workflow_restriction_on!(parent: parent)
  #
  #   # af_file_set = ActiveFedora::Base.find(file_set.id.to_s)
  #   Hyrax.logger.warn "fileset: #{file_set.inspect}"
  #   case file_set
  #   when Hyrax::Resource
  #     @form = Hyrax::Forms::ResourceForm.for(file_set)
  #     @form.prepopulate!
  #     @form[:depositor] = file_set.depositor
  #   else
  #     @form = form_class.new(file_set)
  #     @form[:visibility] = file_set.visibility # workaround for hydra-head < 12
  #   end
  #   Hyrax.logger.warn "form: #{@form.methods.sort}"
  #   @version_list = Hyrax::VersionListPresenter.for(file_set: file_set)
  #   @groups = current_user.groups
  # end

  # def update_metadata
  #   case file_set
  #   when Hyrax::Resource
  #     Hyrax.logger.warn "file_set: #{file_set.inspect}"
  #     Hyrax.logger.warn "required_fields: #{file_set.required_fields.inspect}" if file_set.respond_to?(:required_fields)
  #     change_set = Hyrax::Forms::ResourceForm.for(file_set)
  #     Hyrax.logger.warn("Resolved FileSet change_set class: #{change_set.class.name}")
  #     if change_set.respond_to?(:required_fields)
  #       Hyrax.logger.warn("Resolved FileSet required_fields: #{change_set.required_fields.inspect}")
  #     else
  #       Hyrax.logger.warn('Resolved FileSet change_set does not expose required_fields')
  #     end
  #
  #     Hyrax.logger.warn "change_set: #{change_set}"
  #     Hyrax.logger.warn "change_set methods: #{change_set.methods.sort}"
  #     valid = change_set.validate(attributes)
  #     unless valid
  #       ignored_error = "License can't be blank"
  #       remaining_errors = change_set.errors.full_messages.reject { |message| message == ignored_error }
  #
  #       if remaining_errors.empty?
  #         Hyrax.logger.warn("Ignoring FileSet metadata validation error for #{file_set}: #{ignored_error}")
  #         valid = true
  #       else
  #         Hyrax.logger.error("FileSet metadata validation failed for #{file_set}: #{remaining_errors.join(', ')}")
  #       end
  #     end
  #     return false unless valid
  #
  #     transactions['change_set.apply'].call(change_set).value_or do |result|
  #       Hyrax.logger.error("FileSet metadata apply failed for #{file_set}: #{result.inspect}")
  #       false
  #     end
  #   else
  #     file_attributes = form_class.model_attributes(attributes)
  #     actor.update_metadata(file_attributes)
  #   end
  # end
end
