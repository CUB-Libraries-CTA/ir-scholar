class ApplicationController < ActionController::Base
  def default_url_options
    # if Rails.env.production?
    { host: ENV["ROOT_URL"].split('//')[1] }
    # else
    #  {:host => "localhost:3000"}
    # end
  end

  helper Openseadragon::OpenseadragonHelper
  # Adds a few additional behaviors into the application controller
  include Blacklight::Controller
  skip_after_action :discard_flash_if_xhr
  include Hydra::Controller::ControllerBehavior

  # Adds Hyrax behaviors into the application controller
  include Hyrax::Controller
  include Hyrax::ThemedLayoutController
  with_themed_layout '1_column'
  protect_from_forgery unless: -> { request.format.xml? }
  # protect_from_forgery with: :exception, unless: :http_header_auth?

  # protect_from_forgery with: :exception
end
