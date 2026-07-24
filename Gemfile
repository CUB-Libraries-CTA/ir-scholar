source "https://rubygems.org"

git_source(:github) do |repo_name|
  repo_name = "#{repo_name}/#{repo_name}" unless repo_name.include?("/")
  "https://github.com/#{repo_name}.git"
end

# CU Boulder
# CU Boulder Additions , '3.4.2' #
gem "hydra-derivatives", "~> 3.7.0" # , git: 'https://github.com/samvera/hydra-derivatives.git'

gem "hydra-role-management"
gem "qa"
gem "sidekiq", "~> 6.5.8"
gem "mini_magick"

# SAML
gem "omniauth-saml", git: "https://github.com/culibraries/omniauth-saml.git"

# Recaptcha
gem "recaptcha", require: "recaptcha/rails"

# Production servers
group :production do
  # gem 'mysql2', '~> 0.3.18'
  gem "mysql2"
end

group :development do
  # Use sqlite3 as the database for Active Record
  gem "rubocop", require: false
  # Run "bundle exec standardrb --fix" to auto-fix style violations
  gem "standard", "~> 1.37", require: false
  gem "sqlite3", "~> 1.5.0"
  gem "minitar-cli"
  # Access an IRB console on exception pages or by using <%= console %> anywhere in the code.
  gem "web-console", ">= 3.3.0"
  gem "listen", ">= 3.2.0"
  # Spring speeds up development by keeping your application running in the background. Read more: https://github.com/rails/spring
  gem "spring"
  gem "spring-watcher-listen", "~> 2.0.0"
end

group :development, :test do
  # Call 'byebug' anywhere in the code to stop execution and get a debugger console
  gem "byebug", platforms: [:mri, :mingw, :x64_mingw]
  # Adds support for Capybara system testing
  gem "capybara", ">= 2.15"
end

# Bundle edge Rails instead: gem 'rails', github: 'rails/rails'
gem "rails", "~> 5.2.8"

# Use Puma as the app server
gem "puma", "~> 3.7"
# Use SCSS for stylesheets
gem "sass-rails", "~> 5.0"
# Use Uglifier as compressor for JavaScript assets
gem "uglifier", ">= 1.3.0"
# See https://github.com/rails/execjs#readme for more supported runtimes
# gem 'therubyracer', platforms: :ruby

# Use CoffeeScript for .coffee assets and views
gem "coffee-rails", "~> 4.2"
# Turbolinks makes navigating your web application faster. Read more: https://github.com/turbolinks/turbolinks
gem "turbolinks", "~> 5"
# Build JSON APIs with ease. Read more: https://github.com/rails/jbuilder
gem "jbuilder", "~> 2.5"
# Use Redis adapter to run Action Cable in production
gem "redis", "~> 4.0"
# Use ActiveModel has_secure_password
# gem 'bcrypt', '~> 3.1.7'

# Use Capistrano for deployment
# gem 'capistrano-rails', group: :development

# Windows does not include zoneinfo files, so bundle the tzinfo-data gem
gem "tzinfo-data" # , platforms: [:mingw, :mswin, :x64_mingw, :jruby]

gem "dry-monads", "~> 1.3"
gem "hyrax", "~> 3.6.0"
gem "tinymce-rails", "~> 5.10"
group :development, :test do
  gem "solr_wrapper", ">= 0.3"
end
gem "blacklight_oai_provider", git: "https://github.com/projectblacklight/blacklight_oai_provider.git", branch: "release-6.x"
# gem 'blacklight_oai_provider'

gem "rsolr", ">= 1.0"
gem "bootstrap-sass", "~> 3.0"
gem "twitter-typeahead-rails", "0.11.1.pre.corejavascript"
gem "jquery-rails"
gem "jquery-datatables-rails" # , github: 'rweng/jquery-datatables-rails'
gem "jquery-fileupload-rails", "~> 1.0"
gem "jquery-ui-rails"
gem "devise"
gem "devise-guests", "~> 0.6"
group :development, :test do
  gem "fcrepo_wrapper"
  gem "rspec-rails"
end

# simple_form 5.3.1 broke hydra-editor for certain model types;
#   see: https://github.com/plataformatec/simple_form/issues/1549
gem "simple_form", "~> 5.2.0"

gem "rest-client"

gem "riiif", "~> 2.0"

gem "posix-spawn"
gem "font-awesome-rails"
gem "font-awesome-sass"

gem "active-fedora", "~> 13.3"

gem "sprockets", "= 3.7.2"

gem "ffi", "= 1.17.2"

gem "okcomputer", "~> 1.19"

gem 'bulkrax', '~> 9.1.0'
