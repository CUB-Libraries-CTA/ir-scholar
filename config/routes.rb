Rails.application.routes.draw do
  concern :oai_provider, BlacklightOaiProvider::Routes.new

  mount Riiif::Engine => 'images', as: :riiif if Hyrax.config.iiif_image_server?
  mount Blacklight::Engine => '/'

  concern :searchable, Blacklight::Routes::Searchable.new

  resource :catalog, only: [:index], as: 'catalog', path: '/catalog', controller: 'catalog' do
    concerns :oai_provider

    concerns :searchable
  end

  # Switching enviroment Staging/Production vs Development
  # ========= Staging/Production ==============
  if Rails.env.production?
    devise_for :users, :skip => [:registrations], path_names: { sign_in: 'auth/saml' }, controllers: { omniauth_callbacks: 'users/omniauth_callbacks', sessions: 'users/sessions', registrations: 'users/registrations' }
    devise_scope :user do
      get 'users/auth/saml', to: 'users/omniauth_authorize#passthru', defaults: { provider: :saml }, as: 'new_cu_session'
    end
  end

  # ========= Local Development ==============
  if Rails.env.development?
    devise_for :users, :skip => [:registrations]
  end
  # ==========================================

  mount Hydra::RoleManagement::Engine => '/'

  mount Qa::Engine => '/authorities'
  mount Hyrax::Engine, at: '/'
  resources :welcome, only: 'index'
  root 'hyrax/homepage#index'
  curation_concerns_basic_routes
  concern :exportable, Blacklight::Routes::Exportable.new

  resources :solr_documents, only: [:show], path: '/catalog', controller: 'catalog' do
    concerns :exportable
  end

  resources :bookmarks do
    concerns :exportable

    collection do
      delete 'clear'
    end
  end

  # For details on the DSL available within this file, see http://guides.rubyonrails.org/routing.html
  # mount BrowseEverything::Engine => '/browse'
  # Sidekiq Web App
  require 'sidekiq/web'
  authenticate :user, lambda { |u| u.admin? } do
    mount Sidekiq::Web => '/sidekiq'
  end
  mount Bulkrax::Engine, at: '/'
  # mount Zizia::Engine => '/'
end
