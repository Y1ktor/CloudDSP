#!/usr/bin/env ruby
# Publish reviewed local ARM64 images to one public Docker Hub repository, or
# mirror them back into CloudDSP's loopback registry on a clean machine.
#
# A Docker Hub tag identifies the component and the reviewed source tag. The
# sha256 manifest digest in images.lock.yaml remains authoritative: every
# publish, mirror, and verify step compares the registry's returned digest to
# that lock. A mismatched tag stops rather than being silently overwritten.
# No Docker Hub credential is read here; Docker CLI supplies the user's saved
# login for publication and anonymous pulls serve fresh-machine deployment.
require 'json'
require 'net/http'
require 'open3'
require 'pathname'
require 'uri'
require 'yaml'

class CloudDSPImageRegistryStage
  ROOT = Pathname.new(File.expand_path('..', __dir__)).freeze
  LOCK = ROOT.join('images.lock.yaml').freeze
  LOCAL_HOST = 'clouddsp-registry.localhost:5001'.freeze
  HUB_REPOSITORY = 'y1ktor/clouddsp'.freeze
  MANIFEST_ACCEPT = [
    'application/vnd.oci.image.manifest.v1+json',
    'application/vnd.docker.distribution.manifest.v2+json',
    'application/vnd.oci.image.index.v1+json',
    'application/vnd.docker.distribution.manifest.list.v2+json'
  ].join(', ').freeze

  def initialize(output: $stdout, error: $stderr)
    @output = output
    @error = error
  end

  def run(mode)
    raise 'use plan, verify-source, publish, mirror, or verify' unless
      %w[plan verify-source publish mirror verify].include?(mode)

    entries = load_entries
    if mode == 'plan'
      entries.each { |entry| @output.puts "#{entry.fetch(:key)}: #{entry.fetch(:hub_tag)} @ #{entry.fetch(:digest)}" }
      @output.puts "CloudDSP image plan: #{entries.length} reviewed ARM64 images"
      return 0
    end

    entries.each_with_index do |entry, index|
      process(mode, entry)
      @output.puts "CloudDSP image #{mode}: #{index + 1}/#{entries.length} #{entry.fetch(:key)} verified"
      @output.flush
    end
    0
  rescue StandardError => exception
    @error.puts "CloudDSP image #{mode} stopped: #{safe_error(exception)}"
    1
  end

  private

  def load_entries
    lock = YAML.load_file(LOCK.to_s)
    raise 'image lock has no images mapping' unless lock.is_a?(Hash) && lock['images'].is_a?(Hash)

    entries = lock.fetch('images').each_with_object([]) do |(key, item), result|
      next unless item.is_a?(Hash) && item.fetch('immutableReference', '').start_with?("#{LOCAL_HOST}/")

      source_tag = item.fetch('sourceTag')
      local_ref = item.fetch('immutableReference')
      match = local_ref.match(%r{\A#{Regexp.escape(LOCAL_HOST)}/([a-z0-9._-]+)@(sha256:[0-9a-f]{64})\z})
      raise "images.#{key} has an invalid local digest reference" unless match
      raise "images.#{key} has an invalid source tag" unless source_tag.match?(/\A[a-z0-9][a-z0-9._-]*\z/)
      raise "images.#{key} is not locked for linux/arm64" unless
        item.fetch('supportedPlatforms').any? { |platform| platform.start_with?('linux/arm64') }

      hub_tag = "#{key}-#{source_tag}"
      raise "images.#{key} Docker Hub tag is too long" if hub_tag.length > 128
      result << { key: key, local_repo: match[1], local_ref: local_ref, source_tag: source_tag,
                  digest: match[2], hub_tag: hub_tag }
    end
    raise 'image lock contains no local CloudDSP images' if entries.empty?
    raise 'Docker Hub tags collide' unless entries.map { |entry| entry.fetch(:hub_tag) }.uniq.length == entries.length

    entries
  end

  def process(mode, entry)
    expected = entry.fetch(:digest)
    # Fresh-cluster preparation checks every public source before it creates
    # a cluster. The local registry may not exist yet, so this mode must not
    # query it or require Docker credentials.
    if mode == 'verify-source'
      raise "#{entry.fetch(:key)} is absent from Docker Hub or differs from lock" unless
        hub_manifest_digest(entry.fetch(:hub_tag)) == expected
      return
    end

    local = local_manifest_digest(entry.fetch(:local_repo), expected)
    remote = hub_manifest_digest(entry.fetch(:hub_tag))
    raise "#{entry.fetch(:key)} local digest differs from lock" if local && local != expected
    raise "#{entry.fetch(:key)} Docker Hub tag differs from lock" if remote && remote != expected

    case mode
    when 'publish'
      raise "#{entry.fetch(:key)} is absent from the local registry" unless local == expected
      publish(entry) unless remote == expected
    when 'mirror'
      raise "#{entry.fetch(:key)} is absent from Docker Hub" unless remote == expected
      mirror(entry) unless local == expected
    when 'verify'
      raise "#{entry.fetch(:key)} is absent from the local registry" unless local == expected
      raise "#{entry.fetch(:key)} is absent from Docker Hub" unless remote == expected
    end
  end

  def publish(entry)
    source = entry.fetch(:local_ref)
    destination = "#{HUB_REPOSITORY}:#{entry.fetch(:hub_tag)}"
    ensure_cached(source)
    docker('tag', source, destination)
    output = docker('push', destination)
    reported = output[/digest:\s*(sha256:[0-9a-f]{64})/, 1]
    raise "#{entry.fetch(:key)} Docker push did not preserve the locked digest" unless reported == entry.fetch(:digest)
    raise "#{entry.fetch(:key)} is not publicly pullable at the locked digest" unless
      hub_manifest_digest(entry.fetch(:hub_tag)) == entry.fetch(:digest)
  end

  def mirror(entry)
    source = "#{HUB_REPOSITORY}@#{entry.fetch(:digest)}"
    destination = "#{LOCAL_HOST}/#{entry.fetch(:local_repo)}:#{entry.fetch(:source_tag)}"
    existing_tag = local_manifest_digest(entry.fetch(:local_repo), entry.fetch(:source_tag))
    raise "#{entry.fetch(:key)} local tag already points to another digest" if existing_tag && existing_tag != entry.fetch(:digest)

    docker('pull', source)
    docker('tag', source, destination)
    output = docker('push', destination)
    reported = output[/digest:\s*(sha256:[0-9a-f]{64})/, 1]
    raise "#{entry.fetch(:key)} local push did not preserve the locked digest" unless reported == entry.fetch(:digest)
    raise "#{entry.fetch(:key)} local registry did not return the locked digest" unless
      local_manifest_digest(entry.fetch(:local_repo), entry.fetch(:digest)) == entry.fetch(:digest)
  end

  def ensure_cached(reference)
    _out, _err, status = Open3.capture3('docker', 'image', 'inspect', reference)
    docker('pull', reference) unless status.success?
  end

  def docker(*arguments)
    output, _error, status = Open3.capture3('docker', *arguments)
    raise "Docker #{arguments.first} failed" unless status.success?

    output
  end

  def local_manifest_digest(repository, reference)
    uri = URI("http://#{LOCAL_HOST}/v2/#{repository}/manifests/#{reference}")
    manifest_digest(uri)
  end

  def hub_manifest_digest(tag)
    uri = URI("https://registry-1.docker.io/v2/#{HUB_REPOSITORY}/manifests/#{tag}")
    manifest_digest(uri, hub_token)
  end

  def hub_token
    return @hub_token if @hub_token

    uri = URI('https://auth.docker.io/token')
    uri.query = URI.encode_www_form(service: 'registry.docker.io', scope: "repository:#{HUB_REPOSITORY}:pull")
    response = Net::HTTP.get_response(uri)
    raise 'anonymous Docker Hub token request failed' unless response.code == '200'

    @hub_token = JSON.parse(response.body).fetch('token')
  end

  def manifest_digest(uri, token = nil)
    request = Net::HTTP::Head.new(uri)
    request['Accept'] = MANIFEST_ACCEPT
    request['Authorization'] = "Bearer #{token}" if token
    response = Net::HTTP.start(uri.host, uri.port, use_ssl: uri.scheme == 'https', open_timeout: 15, read_timeout: 30) do |http|
      http.request(request)
    end
    return nil if response.code == '404'
    raise "image manifest check failed for #{uri.host} (HTTP #{response.code})" unless response.code == '200'

    digest = response['Docker-Content-Digest']
    raise "image manifest has no digest at #{uri.host}" unless digest&.match?(/\Asha256:[0-9a-f]{64}\z/)

    digest
  end

  def safe_error(error)
    error.instance_of?(RuntimeError) ? error.message : error.class.to_s
  end
end

if $PROGRAM_NAME == __FILE__
  abort 'Usage: image-registry-stage.rb plan|verify-source|publish|mirror|verify' unless ARGV.length == 1
  exit CloudDSPImageRegistryStage.new.run(ARGV.first)
end
