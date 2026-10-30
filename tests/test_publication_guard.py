# @file: tests/test_publication_guard.py
# @exports: Publication credential and Git-index regression tests
# @depends_on: scripts/check_publication.py, Git
# @purpose: Verify ignored secrets, copied credentials and staged content are checked without disclosure.
import subprocess

import pytest

from scripts.check_publication import scan


@pytest.fixture
def repository(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    (tmp_path / '.gitignore').write_text('.env\nmodels/\n')
    (tmp_path / '.env.example').write_text('SERVICE_API_KEY=\n')
    (tmp_path / '.env').write_text('SERVICE_API_KEY=' + 'local-private-value-' * 2 + '\n')
    return tmp_path


def test_ignored_configuration_and_model_weights_are_not_published(repository):
    (repository / 'models').mkdir()
    (repository / 'models' / 'weights.pt').write_bytes(b'private-model')
    count, findings = scan(repository)
    assert count == 2 and findings == []


def test_copied_local_secret_is_blocked_without_printing_it(repository):
    value = 'local-private-value-' * 2
    (repository / 'source.py').write_text('credential = ' + repr(value))
    _, findings = scan(repository)
    assert any('local SERVICE_API_KEY' in finding for finding in findings)
    assert all(value not in finding for finding in findings)


def test_force_added_environment_file_is_blocked(repository):
    subprocess.run(['git', '-C', str(repository), 'add', '-f', '.env'], check=True)
    _, findings = scan(repository, staged=True)
    assert any('private/generated artifact' in finding for finding in findings)


def test_unknown_unquoted_provider_key_is_blocked(repository):
    value = 'ab' * 16
    (repository / '.env.example').write_text('OTHER_API_KEY=' + value + '\n')
    _, findings = scan(repository)
    assert any('unquoted environment credential' in finding for finding in findings)
    assert all(value not in finding for finding in findings)


def test_staged_secret_is_detected_even_if_working_copy_is_clean(repository):
    token = 'ghp_' + 'a' * 36
    path = repository / 'source.py'
    path.write_text('token = ' + repr(token))
    subprocess.run(['git', '-C', str(repository), 'add', 'source.py'], check=True)
    path.write_text('token = ""')
    assert scan(repository)[1] == []
    _, findings = scan(repository, staged=True)
    assert any('API token' in finding for finding in findings)
    assert all(token not in finding for finding in findings)
