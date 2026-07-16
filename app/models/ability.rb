class Ability
  include Scholar::Ability::CollectionAbility
  include Hydra::Ability
  include Hyrax::Ability
  self.ability_logic += [:everyone_can_create_curation_concerns, :collection_abilities]
  self.admin_group_name = 'admin'

  def custom_permissions
    if admin?
      can [:destroy], ActiveFedora::Base
      can [:create, :show, :add_user, :remove_user, :index, :edit, :update, :destroy], Role
    end
    if collection_manager?
      can :manage, ::Collection
      can :manage_any, ::Collection
      can :create_any, ::Collection
      can :view_admin_show_any, ::Collection
    end
    cannot :manage, ::Collection unless collection_manager?
    can :update, ContentBlock
  end

  def admin?
    user_groups.include? admin_group_name
  end

  def collection_manager?
    user_groups.any? { |x| ["collection_manager", "admin"].include?(x) }
  end

  def can_import_works?
    admin?
  end

  def can_export_works?
    admin?
  end
end
