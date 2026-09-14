config = YAML.load(ERB.new(IO.read(Rails.root + 'config' + 'redis.yml')).result)[Rails.env].with_indifferent_access

redis_conn = { url: "redis://#{config[:host]}:#{config[:port]}/" }

Sidekiq.configure_server do |s|
  s.redis = redis_conn
end

Sidekiq.configure_client do |s|
  s.redis = redis_conn
end

SIDEKIQ_WILL_PROCESSES_JOBS_FILE = Rails.root.join('tmp/sidekiq_process_has_started').freeze

Sidekiq.configure_server do |config|
  # We touch and destroy files in the Sidekiq lifecycle to provide a
  # signal to Kubernetes that we are ready to process jobs or not.
  #
  # Doing this gives us a better sense of when a process is actually
  # alive and healthy, rather than just beginning the boot process.
  config.on(:startup) do
    FileUtils.touch(SIDEKIQ_WILL_PROCESSES_JOBS_FILE)
  end

  config.on(:shutdown) do
    FileUtils.rm_f(SIDEKIQ_WILL_PROCESSES_JOBS_FILE)
  end
end
