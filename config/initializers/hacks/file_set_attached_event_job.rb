# frozen_string_literal: true

## CUBL Change:
Hyrax::FileSetAttachedEventJob.class_eval do
  # Log the event to the fileset's and its container's streams
  def log_event(repo_object)
    repo_object.log_event(event)

    if curation_concern.present?
      curation_concern.log_event(event)
    else
      Hyrax.logger.warn("FileSetAttachedEventJob: skipping parent work event log for FileSet #{repo_object.id}; no curation_concern found")
    end
  end

  def action
    if curation_concern.present?
      "User #{link_to_profile depositor} has attached #{file_link} to #{work_link}"
    else
      Hyrax.logger.warn("FileSetAttachedEventJob: skipping work link for FileSet #{repo_object.id}; no curation_concern found")
      "User #{link_to_profile depositor} has attached #{file_link}"
    end
  end

  private

  def file_link
    link_to file_title, polymorphic_path(repo_object)
  end

  def work_link
    return unless curation_concern.present?

    link_to work_title, polymorphic_path(curation_concern)
  end

  def file_title
    repo_object.title.first
  end

  def work_title
    curation_concern&.title&.first
  end

  def curation_concern
    case repo_object
    when ActiveFedora::Base
      parent = repo_object.in_works&.first
      if parent.nil?
        ids = ActiveFedora::SolrService.query("{!field f=member_ids_ssim}#{repo_object.id}",
                                              fl: ActiveFedora.id_field)
                                       .map { |x| x.fetch(ActiveFedora.id_field) }
        parent = ActiveFedora::Base.find(ids.first.to_s)
      end
      parent
    else
      Hyrax.query_service.find_parents(resource: repo_object).first
    end
  end
end
