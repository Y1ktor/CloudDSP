# Optional Flux verification for two explicitly reviewed native Helm releases.
# Component adapters opt in one instance; HelmRelease and the older
# StatelessRelease keep their original behavior for every other component.
# This helper reads only the HelmRelease API record. Native Helm manifests,
# resource specs, Pod identity, images, and HTTP checks remain parent gates.
require 'json'

module FluxOwnership
  # These are reviewed ownership boundaries, not caller-supplied options.
  # Flux's default storage namespace would create a different native release,
  # so target and storage must both match the existing component namespace.
  BINDINGS = {
    'mailpit' => { label: 'Mailpit', helmrelease_name: 'clouddsp-mailpit',
                   helmrelease_namespace: 'flux-system', release_namespace: 'clouddsp-data' },
    'frontend' => { label: 'Frontend', helmrelease_name: 'clouddsp-frontend',
                    helmrelease_namespace: 'flux-system', release_namespace: 'clouddsp-app' }
  }.transform_values do |binding|
    binding.transform_values!(&:freeze)
    binding[:origin_labels] = {
      'helm.toolkit.fluxcd.io/name' => binding.fetch(:helmrelease_name),
      'helm.toolkit.fluxcd.io/namespace' => binding.fetch(:helmrelease_namespace)
    }.freeze
    binding.freeze
  end.freeze

  def run(mode)
    @flux_ownership_mode = mode
    @flux_ownership_helmrelease = nil
    super
  ensure
    @flux_ownership_mode = nil
  end

  private

  def flux_ownership_binding
    BINDINGS.fetch(flux_ownership_component)
  end

  def check_tools
    super
    binding = flux_ownership_binding
    ensure_true(@component == flux_ownership_component && @release == binding.fetch(:helmrelease_name) &&
                @namespace == binding.fetch(:release_namespace),
                "#{binding.fetch(:label)} Flux adapter requires the reviewed #{binding.fetch(:label)} release identity")
    @flux_ownership_helmrelease = flux_ownership_record
    return unless @flux_ownership_helmrelease

    # Existence reserves this release before its first reconciliation. Failed,
    # suspended, or deleting HelmReleases still own upgrade/uninstall, so direct
    # installation or adoption must stop before any chart or prerequisite work.
    ensure_true(!%w[adopt install].include?(@flux_ownership_mode),
                "#{binding.fetch(:label)} is owned by Flux HelmRelease #{binding.fetch(:helmrelease_namespace)}/#{binding.fetch(:helmrelease_name)}; change its GitOps configuration instead of direct #{@flux_ownership_mode}")
    validate_flux_ownership_record
  end

  def flux_ownership_record
    binding = flux_ownership_binding
    context = self.class::CONTEXT
    # A pre-Flux cluster may explicitly lack the CRD. API/RBAC/network errors
    # propagate through command; they never grant permission for a Helm write.
    crd = command('kubectl', '--context', context, 'get',
                  'customresourcedefinition/helmreleases.helm.toolkit.fluxcd.io',
                  '--ignore-not-found', '--output', 'name')
    return nil if crd.strip.empty?

    output = command('kubectl', '--context', context, '--namespace', binding.fetch(:helmrelease_namespace),
                     'get', "helmrelease.helm.toolkit.fluxcd.io/#{binding.fetch(:helmrelease_name)}",
                     '--ignore-not-found', '--output', 'json')
    return nil if output.strip.empty?

    record = JSON.parse(output)
    ensure_true(record['kind'] == 'HelmRelease' &&
                record.dig('metadata', 'name') == binding.fetch(:helmrelease_name) &&
                record.dig('metadata', 'namespace') == binding.fetch(:helmrelease_namespace),
                "#{binding.fetch(:label)} Flux API returned an unexpected HelmRelease identity")
    record
  end

  def validate_flux_ownership_record
    record = @flux_ownership_helmrelease
    binding = flux_ownership_binding
    expected = { 'releaseName' => binding.fetch(:helmrelease_name),
                 'targetNamespace' => binding.fetch(:release_namespace),
                 'storageNamespace' => binding.fetch(:release_namespace) }
    expected.each do |field, value|
      ensure_true(record.dig('spec', field) == value,
                  "#{binding.fetch(:label)} Flux HelmRelease spec.#{field} must be #{value}")
    end
    ensure_true(record.dig('spec', 'suspend') != true && !record.dig('metadata', 'deletionTimestamp'),
                "#{binding.fetch(:label)} Flux HelmRelease is suspended or deleting; wait for active reconciliation before verification")
    generation = record.dig('metadata', 'generation')
    ready = record.dig('status', 'conditions').to_a.find { |condition| condition['type'] == 'Ready' }
    # Both status and Ready must describe this exact generation. A cached
    # Ready=True from an older spec cannot authorize completed handoff checks.
    ensure_true(generation.is_a?(Integer) && generation.positive? &&
                record.dig('status', 'observedGeneration') == generation &&
                ready && ready['status'] == 'True' && ready['observedGeneration'] == generation,
                "#{binding.fetch(:label)} Flux HelmRelease is not Ready for its current generation")
    ensure_true(record.dig('status', 'storageNamespace') == binding.fetch(:release_namespace),
                "#{binding.fetch(:label)} Flux HelmRelease status.storageNamespace must be #{binding.fetch(:release_namespace)}")
  end

  def check_chart_and_source
    super
    return unless @flux_ownership_helmrelease

    binding = flux_ownership_binding
    revision = @flux_ownership_helmrelease.dig('status', 'lastAttemptedRevision')
    chart_prefix = "#{flux_ownership_component}-"
    base_version = @chart_identity.delete_prefix(chart_prefix)
    # Revision strategy adds only SHA12 and a positive HelmChart generation to
    # the version already reviewed in Chart.yaml. Native Helm must equal the
    # precise revision reconciled by the current Ready HelmRelease.
    ensure_true(@chart_identity == "#{chart_prefix}#{base_version}" && revision.is_a?(String) &&
                revision.match?(/\A#{Regexp.escape(base_version)}\+[0-9a-f]{12}\.[1-9][0-9]*\z/),
                "#{binding.fetch(:label)} Flux chart revision differs from the reviewed chart version or Git revision format")
    @chart_identity = "#{chart_prefix}#{revision}"

    # Flux adds its two exact origin labels to top-level resource metadata.
    # Add them only after strict source/chart equality. Pod templates,
    # selectors, all specs, and every other label retain exact comparisons.
    origin_labels = binding.fetch(:origin_labels)
    [@source, @rendered].each do |objects|
      objects.each_value do |object|
        labels = object.fetch('metadata').fetch('labels')
        ensure_true(origin_labels.keys.none? { |key| labels.key?(key) },
                    "#{binding.fetch(:label)} source/chart must not declare Flux origin labels")
        labels.merge!(origin_labels)
      end
    end
  end

  def release_record
    record = super
    if record && @flux_ownership_helmrelease
      binding = flux_ownership_binding
      ensure_true(record['namespace'] == binding.fetch(:release_namespace),
                  "#{binding.fetch(:label)} native Helm release namespace must be #{binding.fetch(:release_namespace)}")
    end
    record
  end
end
