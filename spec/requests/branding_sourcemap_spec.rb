# frozen_string_literal: true

require 'rails_helper'

RSpec.describe 'Branding sourcemap', type: :request do
  it 'returns no content for the branding sourcemap path' do
    get '/assets/branding.css.map'

    expect(response).to have_http_status(:no_content)
  end
end