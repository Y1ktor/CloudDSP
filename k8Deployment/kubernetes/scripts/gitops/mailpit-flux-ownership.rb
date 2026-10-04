# Mailpit's optional handoff from direct Helm commands to Flux helm-controller.
# Extend only this release instance so other components retain their existing
# adoption, installation, and worker-scaling rules. The adapter uses the same
# private command/check methods in both HelmRelease and its older StatelessRelease
# implementation; it does not read Secret contents or change cluster resources.
require 'json'

module MailpitFluxOwnership
  HELMRELEASE_NAME = 'clouddsp-mailpit'.freeze
  HELMRELEASE_NAMESPACE = 'flux-system'.freeze
  RELEASE_NAMESPACE = 'clouddsp-data'.freeze
  ORIGIN_LABELS = {
    'helm.toolkit.fluxcd.io/name' => HELMRELEASE_NAME,
    'helm.toolkit.fluxcd.io/namespace' => HELMRELEASE_NAMESPACE
  }.freeze

  def run(mode)
    @mailpit_flux_mode = mode
    @mailpit_flux_helmrelease = nil
    super
  ensure
    @mailpit_flux_mode = nil
  end

  private

  def check_tools
    super
    ensure_true(@component == 'mailpit' && @release == HELMRELEASE_NAME && @namespace == RELEASE_NAMESPACE,
                'Mailpit Flux adapter requires the reviewed Mailpit release identity')
    @mailpit_flux_helmrelease = mailpit_flux_record
    return unless @mailpit_flux_helmrelease

    # Existence reserves this release for Flux before its first reconciliation.
    # A suspended, failed, or deleting HelmRelease still owns its lifecycle:
    # direct Helm installation or takeover could race its upgrade/uninstall.
    ensure_true(!%w[adopt install].include?(@mailpit_flux_mode),
                "Mailpit is owned by Flux HelmRelease #{HELMRELEASE_NAMESPACE}/#{HELMRELEASE_NAME}; change its GitOps configuration instead of direct #{@mailpit_flux_mode}")
    validate_mailpit_flux_record
  end

  def mailpit_flux_record
    context = self.class::CONTEXT
    # An ordinary pre-Flux cluster has no HelmRelease CRD. Distinguish that
    # explicit absence from an API/RBAC/network failure, which command raises
    # and the parent runner reports as a failed gate rather than permission
    # to install. No Kubernetes Secret or Helm storage object is inspected.
    crd = command('kubectl', '--context', context, 'get',
                  'customresourcedefinition/helmreleases.helm.toolkit.fluxcd.io',
                  '--ignore-not-found', '--output', 'name')
    return nil if crd.strip.empty?

    output = command('kubectl', '--context', context, '--namespace', HELMRELEASE_NAMESPACE,
                     'get', "helmrelease.helm.toolkit.fluxcd.io/#{HELMRELEASE_NAME}",
                     '--ignore-not-found', '--output', 'json')
    return nil if output.strip.empty?

    record = JSON.parse(output)
    ensure_true(record['kind'] == 'HelmRelease' && record.dig('metadata', 'name') == HELMRELEASE_NAME &&
                record.dig('metadata', 'namespace') == HELMRELEASE_NAMESPACE,
                'Mailpit Flux API returned an unexpected HelmRelease identity')
    record
  end

  def validate_mailpit_flux_record
    record = @mailpit_flux_helmrelease
    # Flux defaults Helm storage to its own namespace. All three explicit
    # fields are required here to reuse the existing native Helm release and
    # its history in clouddsp-data rather than creating a second release.
    expected = { 'releaseName' => HELMRELEASE_NAME,
                 'targetNamespace' => RELEASE_NAMESPACE,
                 'storageNamespace' => RELEASE_NAMESPACE }
    expected.each do |field, value|
      ensure_true(record.dig('spec', field) == value,
                  "Mailpit Flux HelmRelease spec.#{field} must be #{value}")
    end
    ensure_true(record.dig('spec', 'suspend') != true && !record.dig('metadata', 'deletionTimestamp'),
                'Mailpit Flux HelmRelease is suspended or deleting; wait for active reconciliation before verification')
    generation = record.dig('metadata', 'generation')
    ready = record.dig('status', 'conditions').to_a.find { |condition| condition['type'] == 'Ready' }
    # A cached Ready=True from an older spec is insufficient. Both the status
    # and Ready condition must describe this generation before source, native
    # Helm storage, and live workload checks can report a completed handoff.
    ensure_true(generation.is_a?(Integer) && generation.positive? &&
                record.dig('status', 'observedGeneration') == generation &&
                ready && ready['status'] == 'True' && ready['observedGeneration'] == generation,
                'Mailpit Flux HelmRelease is not Ready for its current generation')
    ensure_true(record.dig('status', 'storageNamespace') == RELEASE_NAMESPACE,
                "Mailpit Flux HelmRelease status.storageNamespace must be #{RELEASE_NAMESPACE}")
  end

  def check_chart_and_source
    super
    return unless @mailpit_flux_helmrelease

    revision = @mailpit_flux_helmrelease.dig('status', 'lastAttemptedRevision')
    base_version = @chart_identity.delete_prefix('mailpit-')
    # source-controller's Revision strategy packages this Git chart with the
    # first twelve Git SHA characters and HelmChart generation (valuesFiles
    # makes generation part of the version). Accept only that precise suffix
    # for the reviewed chart, and require native Helm to match the revision
    # the current Ready HelmRelease actually reconciled.
    ensure_true(@chart_identity == "mailpit-#{base_version}" && revision.is_a?(String) &&
                revision.match?(/\A#{Regexp.escape(base_version)}\+[0-9a-f]{12}\.[1-9][0-9]*\z/),
                'Mailpit Flux chart revision differs from the reviewed chart version or Git revision format')
    @chart_identity = "mailpit-#{revision}"

    # Flux applies these origin labels only to each resource's top-level
    # metadata. Add their exact expected values after the parent's strict
    # source-versus-chart comparison. Keep Pod templates, selectors, all specs,
    # and every other label under the existing exact equality checks.
    [@source, @rendered].each do |objects|
      objects.each_value do |object|
        labels = object.fetch('metadata').fetch('labels')
        ensure_true(ORIGIN_LABELS.keys.none? { |key| labels.key?(key) },
                    'Mailpit source/chart must not declare Flux origin labels')
        labels.merge!(ORIGIN_LABELS)
      end
    end
  end

  def release_record
    record = super
    if record && @mailpit_flux_helmrelease
      ensure_true(record['namespace'] == RELEASE_NAMESPACE,
                  "Mailpit native Helm release namespace must be #{RELEASE_NAMESPACE}")
    end
    record
  end
end
