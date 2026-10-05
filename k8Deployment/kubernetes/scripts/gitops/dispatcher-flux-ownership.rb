# Bind only the legacy Demucs publisher to strict Flux/Helm verification.
# Its chart, image-lock key, selector, and source differ from the generic Pod.
require_relative 'dispatcher-flux-verification'

module DispatcherFluxOwnership
  include DispatcherFluxVerification
  ORIGIN_LABELS = FluxOwnership::BINDINGS.fetch('dispatcher').fetch(:origin_labels)
  VALUES_FILE = './k8Deployment/kubernetes/helm/dispatcher/values.yaml'.freeze
  PAUSE_FILE = './k8Deployment/kubernetes/helm/dispatcher/values.smoke-pause.yaml'.freeze

  private

  def flux_ownership_component
    'dispatcher'
  end
end
