#!/usr/bin/env ruby
# Validate the value-free contract for local Secret sources. This command
# reads only committed catalog/example files. It never opens .local, contacts
# Kubernetes, or prints a credential. The initializer uses the same contract
# before creating any Kubernetes resources.
require_relative 'paths'
require 'pathname'
require 'yaml'

class CloudDSPCredentialCatalog
  ROOT = CloudDSPPaths::KUBERNETES_ROOT
  DEFAULT_PATH = ROOT.join('credentials', 'catalog.yaml').freeze
  SOURCE_NAME = /\A[a-z0-9][a-z0-9-]*\.secret\.yaml\z/.freeze
  EXAMPLE_NAME = /\A(?:services|helm)\/[a-z0-9\/-]+\.secret\.example\.yaml\z/.freeze
  FIELD_NAME = /\A[A-Z][A-Z0-9_]*\z/.freeze
  GROUP_NAME = /\A[a-z0-9][a-z0-9-]*\z/.freeze
  DERIVED_AMQP_KEYS = %w[scheme host port vhost username password].freeze

  def initialize(catalog_path: DEFAULT_PATH, root: ROOT, output: $stdout, error: $stderr)
    @catalog_path = Pathname.new(catalog_path)
    @root = Pathname.new(root)
    @output = output
    @error = error
  end

  def run
    catalog = load_yaml(@catalog_path)
    check(catalog.is_a?(Hash) && catalog.keys.sort == %w[credentials version], 'catalog structure differs')
    check(catalog['version'] == 1, 'catalog version is unsupported')
    groups = catalog['credentials']
    check(groups.is_a?(Hash) && !groups.empty?, 'credential groups are absent')

    files = []
    examples = []
    runtime = 0
    bootstrap = 0
    groups.each do |name, group|
      check(name.is_a?(String) && GROUP_NAME.match?(name), 'credential group name is invalid')
      check(group.is_a?(Hash) && group.keys.sort == %w[fields sources], "#{name} structure differs")
      fields = check_fields(name, group['fields'])
      sources = group['sources']
      check(sources.is_a?(Array) && !sources.empty?, "#{name} sources are absent")
      roles = sources.map { |source| source.is_a?(Hash) ? source['role'] : nil }
      check(roles.count('runtime') == 1 && roles.count('bootstrap') <= 1 &&
            roles.all? { |role| %w[runtime bootstrap].include?(role) },
            "#{name} must have one runtime source and at most one bootstrap source")

      sources.each do |source|
        file, example = check_source(name, source, fields)
        files << file
        examples << example
        source['role'] == 'runtime' ? runtime += 1 : bootstrap += 1
      end
    end
    check(files.uniq.length == files.length, 'a local Secret filename is assigned twice')
    check(examples.uniq.length == examples.length, 'an example Secret is assigned twice')
    @output.puts "CloudDSP credential catalog verified: #{groups.length} groups, #{runtime} runtime sources, #{bootstrap} bootstrap sources; no private files read"
    0
  rescue StandardError => exception
    @error.puts "CloudDSP credential catalog invalid: #{exception.instance_of?(RuntimeError) ? exception.message : exception.class}"
    1
  end

  private

  def check_fields(name, fields)
    check(fields.is_a?(Hash) && !fields.empty?, "#{name} has no fields")
    fields.each do |key, rule|
      check(key.is_a?(String) && FIELD_NAME.match?(key), "#{name} has an invalid field name")
      check(rule.is_a?(Hash) && rule.length == 1, "#{name}/#{key} must have one rule")
      kind, value = rule.first
      private_field = key.match?(/(?:PASSWORD|SECRET_KEY|_PASS\z|_URL_)/)
      check(!private_field || %w[generate derive].include?(kind),
            "#{name}/#{key} must not contain a public credential value")
      case kind
      when 'fixed', 'default'
        check(value.is_a?(String) && !value.empty? && !value.include?("\n") &&
              !value.include?('REPLACE_'), "#{name}/#{key} has an invalid public value")
      when 'generate'
        check(%w[password secret_key].include?(value), "#{name}/#{key} has an unknown generator")
      when 'derive'
        check(value.is_a?(Hash) && value.keys.sort == DERIVED_AMQP_KEYS.sort &&
              value['scheme'] == 'amqp' && value['host'].is_a?(String) &&
              value['port'] == 5672 && value['vhost'] == '/clouddsp' &&
              %w[username password].all? { |field| fields.key?(value[field]) },
              "#{name}/#{key} has an invalid derived AMQP URL rule")
      else
        check(false, "#{name}/#{key} has an unknown rule")
      end
    end
    fields
  end

  def check_source(name, source, fields)
    check(source.is_a?(Hash) && source.keys.sort == %w[example file role],
          "#{name} source structure differs")
    file = source['file']
    example = source['example']
    check(file.is_a?(String) && SOURCE_NAME.match?(file), "#{name} local filename is invalid")
    check(example.is_a?(String) && EXAMPLE_NAME.match?(example), "#{name} example path is invalid")
    example_path = @root.join(example)
    check(example_path.file?, "#{name} example is absent: #{example}")
    manifest = load_yaml(example_path)
    metadata = manifest.is_a?(Hash) ? manifest['metadata'] : nil
    values = manifest.is_a?(Hash) ? manifest['stringData'] : nil
    check(manifest.is_a?(Hash) && manifest['apiVersion'] == 'v1' && manifest['kind'] == 'Secret' &&
          manifest['type'] == 'Opaque' && manifest['data'].nil? &&
          metadata.is_a?(Hash) && metadata['name'].is_a?(String) &&
          metadata['name'].start_with?('clouddsp-') &&
          file == "#{metadata['name'].sub(/\Aclouddsp-/, '')}.secret.yaml" &&
          %w[clouddsp-app clouddsp-data].include?(metadata['namespace']) &&
          values.is_a?(Hash) && values.keys.sort == fields.keys.sort,
          "#{name} example identity or fields differ: #{example}")
    check(source['role'] != 'bootstrap' || metadata['namespace'] == 'clouddsp-data',
          "#{name} bootstrap Secret must be in clouddsp-data")
    fields.each do |key, rule|
      example_value = values[key]
      check(example_value.is_a?(String), "#{name}/#{key} example value is invalid")
      kind, value = rule.first
      expected = if kind == 'fixed'
                   example_value == value
                 elsif kind == 'default'
                   example_value == value || example_value.include?('REPLACE_')
                 else
                   example_value.include?('REPLACE_')
                 end
      check(expected, "#{name}/#{key} example contract differs")
    end
    [file, example]
  end

  def load_yaml(path)
    YAML.safe_load(path.read)
  end

  def check(condition, message)
    raise message unless condition
  end
end

if $PROGRAM_NAME == __FILE__
  abort 'Usage: credential-catalog.rb (no arguments)' unless ARGV.empty?
  exit CloudDSPCredentialCatalog.new.run
end
