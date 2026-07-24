namespace :etd_import do
  task :upload, [:filepath] => [:environment] do |t, args|
    csv_file = args[:filepath]
    ModularImporter.new(csv_file).import
  end
end
