# Bind the route-aware publisher to the shared strict Flux/pause verifier.
# Its explicit runtime command and generic image remain source-checked.
require_relative 'dispatcher-flux-verification'

module GenericDispatcherFluxOwnership
  include DispatcherFluxVerification
  ORIGIN_LABELS = FluxOwnership::BINDINGS.fetch('generic-dispatcher').fetch(:origin_labels)
  VALUES_FILE = './k8Deployment/kubernetes/helm/generic-dispatcher/values.yaml'.freeze
  PAUSE_FILE = './k8Deployment/kubernetes/helm/generic-dispatcher/values.smoke-pause.yaml'.freeze

  private

  def flux_ownership_component
    'generic-dispatcher'
  end
end
