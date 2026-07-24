config = YAML.load(ERB.new(IO.read(Rails.root + 'config' + 'redis.yml')).result)[Rails.env].with_indifferent_access

redis_conn = { url: "redis://#{config[:host]}:#{config[:port]}/" }

Sidekiq.configure_server do |s|
  s.redis = redis_conn

  # If SIDEKIQ_QUEUES exists, populate :queues
  # If not, generate a default for :queues
  if ENV["SIDEKIQ_QUEUES"]
    queues = []
    ENV["SIDEKIQ_QUEUES"].split(/\s+/).each do |queue_weighted|
      queue, count = queue_weighted.split(",")
      count = count.to_i
      [count, 1].max.times do
        queues.push(queue)
      end
    end

    s.options[:queues] = queues
  else
    s.options[:queues] = ["default", "ingest", "import", "export"]
  end
end

Sidekiq.configure_client do |s|
  s.redis = redis_conn
end
