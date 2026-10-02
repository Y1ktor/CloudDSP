#!/usr/bin/env ruby
# Render the fresh platform's ignored Secret sources from the reviewed,
# value-free credential catalog. The command never contacts Kubernetes and
# never replaces a source that already exists. All values are prepared and
# checked in memory before owner-only files are linked into .local.
require 'fileutils'
require 'pathname'
require 'securerandom'
require 'stringio'
require 'tmpdir'
require 'uri'
require 'yaml'
require_relative 'credential-catalog'

class CloudDSPCredentialInit
  ROOT = CloudDSPCredentialCatalog::ROOT
  CATALOG_PATH = CloudDSPCredentialCatalog::DEFAULT_PATH
  LOCAL_DIRECTORY = ROOT.parent.join('.local').freeze
  INPUT_KEYS = %w[version credentials].freeze
  PASSWORD_LENGTH = 8

  def initialize(local_directory: LOCAL_DIRECTORY, input_path: nil, catalog_path: CATALOG_PATH,
                 root: ROOT, output: $stdout, error: $stderr)
    @local_directory = Pathname.new(local_directory)
    @input_path = input_path && Pathname.new(input_path)
    @catalog_path = Pathname.new(catalog_path)
    @root = Pathname.new(root)
    @output = output
    @error = error
  end

  def run
    # Refuse to generate from a drifted catalog or example. Capture the
    # validator's success line so init has one concise, value-free result.
    contract_errors = StringIO.new
    valid = CloudDSPCredentialCatalog.new(catalog_path: @catalog_path, root: @root,
                                          output: StringIO.new, error: contract_errors).run
    check(valid.zero?, contract_errors.string.strip)
    groups = YAML.safe_load(@catalog_path.read).fetch('credentials')
    sources = groups.values.flat_map { |group| group.fetch('sources') }
    check(!@local_directory.symlink?, 'local credential directory must not be a symlink')
    present = sources.select { |source| @local_directory.join(source.fetch('file')).exist? }

    if present.length == sources.length
      check(@input_path.nil?, 'credentials already exist; input overrides cannot change them')
      validate_existing(groups)
      @output.puts "CloudDSP credential sources already initialized: #{sources.length} files verified; no values changed"
      # Manually created sources can predate this initializer. Report broad
      # permissions without changing a working installation's files.
      insecure = (@local_directory.stat.mode & 0o077) != 0 || sources.any? do |source|
        (@local_directory.join(source.fetch('file')).stat.mode & 0o077) != 0
      end
      @output.puts 'CloudDSP credential warning: existing .local permissions allow group or other access; use chmod 700 on the directory and chmod 600 on its Secret files' if insecure
      return 0
    end
    check(present.empty?, "partial credential set: #{present.length}/#{sources.length} source files exist; no values changed")

    overrides = load_overrides(groups)
    rendered = render(groups, overrides)
    prepare_directory
    publish(rendered)
    @output.puts "CloudDSP credential sources initialized: #{rendered.length} owner-only files in k8Deployment/.local; no values printed"
    0
  rescue StandardError => exception
    @error.puts "CloudDSP credential initialization stopped: #{exception.instance_of?(RuntimeError) ? exception.message : exception.class}"
    1
  end

  private

  def load_overrides(groups)
    return {} if @input_path.nil?

    check(@input_path.file? && !@input_path.symlink?, 'credential input must be a regular file')
    check((@input_path.stat.mode & 0o077).zero?, 'credential input must have owner-only permissions')
    input = YAML.safe_load(@input_path.read)
    check(input.is_a?(Hash) && input.keys.sort == INPUT_KEYS.sort && input['version'] == 1 &&
          input['credentials'].is_a?(Hash), 'credential input structure differs')
    overrides = input.fetch('credentials')
    overrides.each do |group_name, values|
      check(groups.key?(group_name), 'unknown credential input group')
      check(values.is_a?(Hash), "#{group_name} input must be a mapping")
      values.each do |key, value|
        rule = groups.fetch(group_name).fetch('fields')[key]
        check(rule && %w[default generate].include?(rule.keys.first),
              "#{group_name} input contains a field that cannot be overridden")
        validate_value(group_name, key, value, rule.keys.first, fresh: true)
      end
    end
    overrides
  end

  def render(groups, overrides)
    rendered = {}
    groups.each do |group_name, group|
      fields = group.fetch('fields')
      chosen = overrides.fetch(group_name, {})
      values = {}
      fields.each do |key, rule|
        kind, setting = rule.first
        next if kind == 'derive'

        value = if chosen.key?(key)
                  chosen.fetch(key)
                elsif kind == 'generate'
                  # Forty hexadecimal characters give 160 random bits and
                  # satisfy the PostgreSQL, RabbitMQ, Keycloak, and MinIO
                  # credential formats without quoting or URL ambiguity.
                  SecureRandom.hex(20)
                else
                  setting
                end
        validate_value(group_name, key, value, kind, fresh: true)
        values[key] = value
      end
      fields.each do |key, rule|
        next unless rule.key?('derive')
        values[key] = amqp_url(rule.fetch('derive'), values)
      end
      group.fetch('sources').each do |source|
        manifest = YAML.safe_load(@root.join(source.fetch('example')).read)
        manifest['stringData'] = values.dup
        rendered[source.fetch('file')] = YAML.dump(manifest)
      end
    end
    rendered
  end

  def validate_existing(groups)
    groups.each do |group_name, group|
      reference = nil
      group.fetch('sources').each do |source|
        path = @local_directory.join(source.fetch('file'))
        check(path.file? && !path.symlink?, "#{group_name} local source is not a regular file")
        manifest = YAML.safe_load(path.read)
        example = YAML.safe_load(@root.join(source.fetch('example')).read)
        values = manifest.is_a?(Hash) ? manifest['stringData'] : nil
        check(manifest.is_a?(Hash) && %w[apiVersion kind metadata type].all? { |key|
                manifest[key] == example[key]
              } && manifest['data'].nil? && values.is_a?(Hash) &&
              values.keys.sort == group.fetch('fields').keys.sort,
              "#{group_name} local source contract differs")
        group.fetch('fields').each do |key, rule|
          kind, setting = rule.first
          validate_value(group_name, key, values[key], kind, fresh: false)
          check(values[key] == setting, "#{group_name}/#{key} fixed identity differs") if kind == 'fixed'
          placeholder = example.fetch('stringData').fetch(key)
          if kind == 'generate' || kind == 'derive' || (kind == 'default' && placeholder.include?('REPLACE_'))
            check(values[key] != placeholder,
                  "#{group_name}/#{key} still uses an example placeholder")
          end
          check(values[key] == amqp_url(setting, values),
                "#{group_name}/#{key} derived URL differs") if kind == 'derive'
        end
        check(reference.nil? || reference == values,
              "#{group_name} runtime and bootstrap values differ")
        reference = values
      end
    end
  end

  def validate_value(group_name, key, value, kind, fresh:)
    check(value.is_a?(String) && !value.empty? && !value.include?("\n") &&
          !value.include?('REPLACE_'), "#{group_name}/#{key} has an invalid value")
    if kind == 'generate'
      check(value.length >= (fresh ? 16 : PASSWORD_LENGTH),
            "#{group_name}/#{key} is too short")
    elsif kind == 'default'
      check(value.match?(/\A[A-Za-z0-9][A-Za-z0-9_-]{2,63}\z/) &&
            !(group_name == 'rabbitmq' && value == 'guest'),
            "#{group_name}/#{key} has an invalid username")
    end
  end

  def amqp_url(rule, values)
    user = URI::DEFAULT_PARSER.escape(values.fetch(rule.fetch('username')), /[^A-Za-z0-9._~-]/)
    password = URI::DEFAULT_PARSER.escape(values.fetch(rule.fetch('password')), /[^A-Za-z0-9._~-]/)
    vhost = URI::DEFAULT_PARSER.escape(rule.fetch('vhost'), /[^A-Za-z0-9._~-]/)
    "#{rule.fetch('scheme')}://#{user}:#{password}@#{rule.fetch('host')}:#{rule.fetch('port')}/#{vhost}"
  end

  def prepare_directory
    FileUtils.mkdir_p(@local_directory, mode: 0o700)
    check(@local_directory.directory? && !@local_directory.symlink? &&
          (@local_directory.stat.mode & 0o077).zero? &&
          @local_directory.stat.uid == Process.uid,
          'local credential directory must be owned by this user and inaccessible to others')
  end

  def publish(rendered)
    created = []
    # Hard-linking a complete staged file into its final name fails if that
    # name appeared concurrently. A failure removes only files we created;
    # existing sources are never overwritten.
    Dir.mktmpdir('clouddsp-credentials-', @local_directory.to_s) do |temporary|
      staged = rendered.map do |name, content|
        path = File.join(temporary, name)
        File.open(path, File::WRONLY | File::CREAT | File::EXCL, 0o600) { |file| file.write(content) }
        [path, @local_directory.join(name).to_s]
      end
      begin
        staged.each do |source, target|
          File.link(source, target)
          created << target
        end
      rescue StandardError
        created.each { |path| File.delete(path) if File.exist?(path) }
        raise
      end
    end
  end

  def check(condition, message)
    raise message unless condition
  end
end

if $PROGRAM_NAME == __FILE__
  abort 'Usage: credential-init.rb [--input OWNER_ONLY_YAML]' unless
    ARGV.empty? || (ARGV.length == 2 && ARGV.first == '--input' && !ARGV.last.empty?)
  input = ARGV.empty? ? nil : ARGV.last
  exit CloudDSPCredentialInit.new(input_path: input).run
end
