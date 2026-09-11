namespace :sipity do
  desc "Run parallel sipity:cleanup_sipity_threaded rake tasks to crawl through the sipity entities faster"
  task :cleanup_sipity_threaded, [:proc_count] => :environment do |_t, args|
    args.with_defaults({ proc_count: 4 })

    Sipity::Entity.all.in_groups(args.proc_count.to_i).each_with_index do |entities, index|
      File.open("./tmp/entities_#{index}", 'w+') do |file|
        Process.fork do
          complete = 0
          entities.each do |s|
            complete += 1
            if (complete % 100).zero?
              puts "Completed #{complete} out of #{entities.count}"
            end
            begin
              GlobalID::Locator.locate(s.proxy_for_global_id)
            rescue Ldp::Gone => e
              file.puts s.id
              s.delete
              next
            rescue => e
              hyrax.logger.error "Error processing #{s.id} #{e.message}"
              next
            end
          end
        end
      end
    end
  end
end
