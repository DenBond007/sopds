#!/usr/bin/env python3
"""Fail closed unless GHCR packages are private (or absent before first publication)."""
import argparse
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


def request_json(url, token=None):
    headers = {'Accept': 'application/vnd.github+json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    with urlopen(Request(url, headers=headers), timeout=30) as response:
        return json.load(response)


def check_package(owner, package, token, allow_missing=False):
    # This repository belongs to a personal account. Do not guess an organization endpoint.
    url = 'https://api.github.com/users/' + quote(owner, safe='') + '/packages/container/' + quote(package, safe='')
    try:
        value = request_json(url, token)
    except HTTPError as error:
        if error.code == 404 and allow_missing:
            print(package + ': not present or not accessible')
            return False
        raise RuntimeError('Cannot confirm package visibility (HTTP %s)' % error.code) from error
    if value.get('visibility') != 'private':
        raise RuntimeError(package + ': expected private visibility, got ' + repr(value.get('visibility')) + '; publication refused')
    print(package + ': private')
    return True


def check_anonymous_denied(reference):
    path, digest = reference.removeprefix('ghcr.io/').split('@', 1)
    url = 'https://ghcr.io/token?' + urlencode({'service': 'ghcr.io', 'scope': 'repository:' + path + ':pull'})
    try:
        value = request_json(url)
        token = value.get('token') or value['access_token']
        request = Request('https://ghcr.io/v2/' + path + '/manifests/' + digest,
                          headers={'Authorization': 'Bearer ' + token,
                                   'Accept': 'application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json'},
                          method='HEAD')
        with urlopen(request, timeout=30):
            pass
    except HTTPError as error:
        if error.code in (401, 403):
            print(path + ': anonymous pull denied')
            return
        raise RuntimeError('Anonymous check inconclusive (HTTP %s)' % error.code) from error
    raise RuntimeError('Anonymous pull is allowed; private publication requirement failed')


def initialize_private_package(owner, package, token):
    if check_package(owner, package, token, allow_missing=True):
        return
    # Prove the registry's actual visibility before uploading any application layers.
    # This seed contains only an empty scratch config, no source, files or secrets.
    image = 'ghcr.io/' + owner.lower() + '/' + package
    with TemporaryDirectory() as folder:
        root = Path(folder)
        (root / 'Dockerfile').write_text('FROM scratch\nLABEL io.sopds.purpose="empty registry privacy probe"\n')
        metadata = root / 'metadata.json'
        subprocess.run(['docker', 'buildx', 'build', '--platform', 'linux/amd64', '--provenance=false',
                        '--metadata-file', str(metadata), '--tag', image + ':registry-bootstrap',
                        '--push', str(root)], check=True)
        digest = json.loads(metadata.read_text())['containerimage.digest']
    check_package(owner, package, token)
    check_anonymous_denied(image + '@' + digest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner', required=True)
    parser.add_argument('--package', action='append', required=True)
    parser.add_argument('--allow-missing', action='store_true')
    parser.add_argument('--initialize', action='store_true')
    parser.add_argument('--image', action='append', default=[])
    args = parser.parse_args()
    token = os.environ['GH_TOKEN']
    for package in args.package:
        if args.initialize:
            initialize_private_package(args.owner, package, token)
        else:
            check_package(args.owner, package, token, args.allow_missing)
    for reference in args.image:
        check_anonymous_denied(reference)


if __name__ == '__main__':
    main()
