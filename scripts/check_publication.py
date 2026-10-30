# @file: scripts/check_publication.py
# @exports: scan, main
# @depends_on: Python standard library, Git, optional local .env
# @purpose: Check publishable/index content for local credentials, token patterns and private artifacts.
import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 100 * 1024 * 1024
PRIVATE_PARTS = {'.venv', 'venv', 'node_modules', 'models', 'checkpoints', 'resume-updated',
                 '__pycache__', '.aws', '.codex', '.agents', 'logs', 'tmp', 'backups'}
PRIVATE_NAMES = {'PROJECT_STATE.md', 'CODE_MAP.md', 'provider_pause_settings.json'}
PRIVATE_SUFFIXES = {'.pem', '.key', '.p12', '.pfx', '.safetensors', '.ckpt', '.pt', '.pth',
                    '.gguf', '.onnx', '.db', '.dump', '.zip'}
PATTERNS = {
    'private key': re.compile(rb'-{5}BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-{5}'),
    'API token': re.compile(rb'\b(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}|'
                            rb'AIza[0-9A-Za-z_-]{35}|gh[pousr]_[A-Za-z0-9]{30,}|'
                            rb'github_pat_[A-Za-z0-9_]{40,}|xox[baprs]-[A-Za-z0-9-]{20,}|'
                            rb'AKIA[0-9A-Z]{16})\b'),
    'credential assignment': re.compile(
        rb'''(?i)["']?(?:[A-Z0-9_]*(?:API_KEY|AUTH_TOKEN|BEARER_TOKEN|CLIENT_SECRET)|'''
        rb'''api_key|password|secret)["']?\s*[:=]\s*["']([A-Za-z0-9_./+=%-]{24,})["']'''),
    'unquoted environment credential': re.compile(
        rb'(?im)^[ \t]*(?:export\s+)?[A-Z0-9_]*(?:API_KEY|AUTH_TOKEN|BEARER_TOKEN|CLIENT_SECRET|PASSWORD)'
        rb'\s*=\s*[A-Za-z0-9_./+=%-]{24,}[ \t\r]*(?:#[^\n]*)?$'),
}


def git(root, *args, data=None):
    result = subprocess.run(['git', '-C', str(root), *args], input=data, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError('Git command failed; run this check inside an initialized repository.')
    return result.stdout


def staged_blobs(root):
    entries = {}
    for entry in filter(None, git(root, 'ls-files', '--stage', '-z').split(b'\0')):
        metadata, relative = entry.split(b'\t', 1)
        mode, oid, stage = metadata.split()
        if stage != b'0':
            raise RuntimeError('Resolve index conflicts before publication.')
        entries[relative.decode('utf-8')] = (mode, oid)
    if not entries:
        return {}
    request = b'\n'.join(sorted({oid for _, oid in entries.values()})) + b'\n'
    sizes = {row.split()[0]: int(row.split()[2]) for row in
             git(root, 'cat-file', '--batch-check', data=request).splitlines()}
    readable = [oid for oid, size in sizes.items() if size < MAX_BYTES]
    raw = git(root, 'cat-file', '--batch', data=b'\n'.join(readable) + b'\n') if readable else b''
    blobs = {}
    position = 0
    for _ in readable:
        newline = raw.index(b'\n', position)
        oid, _, size = raw[position:newline].split()
        position = newline + 1
        blobs[oid] = raw[position:position + int(size)]
        position += int(size) + 1
    return {name: (mode, sizes[oid], blobs.get(oid, b'')) for name, (mode, oid) in entries.items()}


def env_values(path):
    if not path.is_file():
        return {}
    values = {}
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        match = re.match(r'\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)', line)
        if match:
            values[match[1]] = match[2].strip().strip('\"\'')
    return values


def local_secrets(root):
    example = env_values(root / '.env.example')
    private = env_values(root / '.env')
    return {key: value.encode() for key, value in private.items()
            if re.search(r'KEY|TOKEN|PASSWORD|SECRET|DATABASE_URL|REDIS_URL', key)
            and len(value) >= 8 and value != example.get(key)}


def forbidden(path):
    name = path.name
    return (bool(set(path.parts) & PRIVATE_PARTS) or name in PRIVATE_NAMES
            or (name.startswith('.env') and name != '.env.example')
            or path.suffix.lower() in PRIVATE_SUFFIXES
            or name.startswith(('credentials', 'service-account'))
            or name.endswith(('.log', '.tsbuildinfo')))


def scan(root=ROOT, staged=False):
    indexed = staged_blobs(root) if staged else {}
    paths = sorted(indexed) if staged else sorted(set(filter(None, git(
        root, 'ls-files', '-z', '--cached', '--others', '--exclude-standard').decode('utf-8').split('\0'))))
    secrets = local_secrets(root)
    findings = []
    inspected = 0
    for relative in paths:
        path = root / relative
        if not staged and not path.is_file():
            continue
        inspected += 1
        if forbidden(Path(relative)):
            findings.append(f'{relative}: private/generated artifact must be excluded')
            continue
        if (staged and indexed[relative][0] == b'120000') or path.is_symlink():
            findings.append(f'{relative}: review symlink before publication')
            continue
        size = indexed[relative][1] if staged else path.stat().st_size
        if size >= MAX_BYTES:
            findings.append(f'{relative}: exceeds the 100 MiB publication limit')
            continue
        raw = indexed[relative][2] if staged else path.read_bytes()
        for key, value in secrets.items():
            if value in raw:
                findings.append(f'{relative}: contains a local {key} value (redacted)')
        if b'\0' in raw:
            continue
        for kind, pattern in PATTERNS.items():
            for match in pattern.finditer(raw):
                line = raw.count(b'\n', 0, match.start()) + 1
                findings.append(f'{relative}:{line}: possible {kind} (redacted)')
    return inspected, findings


def main():
    parser = argparse.ArgumentParser(description='Check Git publication candidates without printing secrets.')
    parser.add_argument('--staged', action='store_true', help='Inspect index bytes, including force-added files.')
    args = parser.parse_args()
    count, findings = scan(staged=args.staged)
    if args.staged and count == 0:
        findings.append('No files are staged; stage the intended files before checking the index.')
    for finding in findings:
        print(f'BLOCKED: {finding}')
    print(f'Inspected {count} files; {len(findings)} findings. Secret values are never printed.')
    raise SystemExit(bool(findings))


if __name__ == '__main__':
    main()
