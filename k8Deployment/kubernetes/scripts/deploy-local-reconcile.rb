#!/usr/bin/env ruby
# Existing-cluster root reconciliation is deliberately narrower than a fresh
# install. It reuses the fixed verification order but grants reconcile only to
# the audited Job API PostgreSQL, RabbitMQ, and narrow MinIO bucket/notification
# runners. A missing data bucket is never recreated as an empty replacement.
require_relative 'deploy-local-verify'

exit CloudDSPLocalVerify.new(mode: 'reconcile').run
