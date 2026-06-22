# frozen_string_literal: true

## CUBL Change:
# Modify the fetch_parent_presenter method so that they are fetched using
# ActiveFedora::SolrService instead of the newer Hyrax::SolrService to force
# using ActiveFedora data as is since the Wings/Valkyrie versions aren't
# always compatible in Hyrax v3.x
Hyrax::FileSetPresenter.class_eval do
  def fetch_parent_presenter
    ids = ActiveFedora::SolrService.query("{!field f=member_ids_ssim}#{id}",
                                          fl: ActiveFedora.id_field)
                                   .map { |x| x.fetch(ActiveFedora.id_field) }

    Hyrax.logger.warn("Couldn't find a parent work for FileSet: #{id}.") if ids.empty?
    ids.each do |id|
      doc = ::SolrDocument.find(id)
      next if current_ability.can?(:edit, doc)
      raise WorkflowAuthorizationException if doc.suppressed? && current_ability.can?(:read, doc)
    end

    Hyrax::PresenterFactory.build_for(ids: ids,
                                      presenter_class: Hyrax::WorkShowPresenter,
                                      presenter_args: current_ability).first
  end
end
