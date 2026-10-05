require 'minitest/autorun'
require 'stringio'
require 'tempfile'
require 'yaml'

require_relative '../../scripts/lib/credential-catalog'

class CredentialCatalogTest < Minitest::Test
  def setup
    @catalog = YAML.safe_load(CloudDSPCredentialCatalog::DEFAULT_PATH.read)
    @output = StringIO.new
    @error = StringIO.new
  end

  def run_catalog(catalog = @catalog)
    Tempfile.create(['clouddsp-credential-catalog-', '.yaml']) do |file|
      file.write(YAML.dump(catalog))
      file.flush
      return CloudDSPCredentialCatalog.new(catalog_path: file.path,
                                           output: @output, error: @error).run
    end
  end

  def test_all_fresh_platform_secret_sources_match_reviewed_examples
    assert_equal 0, run_catalog
    assert_includes @output.string, '24 groups, 24 runtime sources, 9 bootstrap sources'
    assert_includes @output.string, 'no private files read'
    assert_empty @error.string
  end

  def test_repeated_local_filename_is_rejected
    @catalog.fetch('credentials')['postgresql-copy'] =
      Marshal.load(Marshal.dump(@catalog.fetch('credentials').fetch('postgresql')))

    assert_equal 1, run_catalog
    assert_includes @error.string, 'assigned twice'
  end

  def test_example_key_drift_is_rejected
    @catalog.fetch('credentials').fetch('postgresql').fetch('fields').delete('POSTGRES_PASSWORD')

    assert_equal 1, run_catalog
    assert_includes @error.string, 'example identity or fields differ'
  end

  def test_unreviewed_generator_is_rejected
    @catalog.fetch('credentials').fetch('postgresql').fetch('fields').fetch('POSTGRES_PASSWORD')['generate'] = 'shell'

    assert_equal 1, run_catalog
    assert_includes @error.string, 'unknown generator'
  end

  def test_catalog_cannot_contain_a_password_value
    @catalog.fetch('credentials').fetch('postgresql').fetch('fields')['POSTGRES_PASSWORD'] =
      { 'fixed' => 'would-be-credential' }

    assert_equal 1, run_catalog
    assert_includes @error.string, 'must not contain a public credential value'
  end
end
