# frozen_string_literal: true

## CUBL Change:
# This fixes how FileSetEditForms retrieves the parent work
Hyrax::EditPermissionsService.class_eval do
  def self.build_service_object_from(form:, ability:)
    if form.object.respond_to?(:model) && form.object.model.work?
      # The provided form object is a work form.
      new(object: form.object, ability: ability)
    elsif form.object.respond_to?(:model) && form.object.model.file_set?
      # The provided form object is a FileSet form. For Valkyrie forms,
      # Hyrax::Forms::FileSetForm, :in_works_ids is prepopulated onto
      # the form object itself. For Hyrax::Forms::FileSetEditForm, the
      # :in_works method is present on the wrapped :model.
      if form.object.is_a?(Hyrax::Forms::FileSetForm)
        object_id = form.object.in_works_ids.first
        new(object: Hyrax.query_service.find_by(id: object_id), ability: ability)
      else
        ids = ActiveFedora::SolrService.query("{!field f=member_ids_ssim}#{form.object.model.id}",
                                              fl: ActiveFedora.id_field)
                                       .map { |x| x.fetch(ActiveFedora.id_field) }
        af_work = ActiveFedora::Base.find(ids.first.to_s)

        new(object: af_work, ability: ability)
      end
    elsif form.object.file_set?
      # The provided form object is a FileSet.
      new(object: form.object.in_works.first, ability: ability)
    end
  end
end
