#!/usr/bin/env python3
"""Render the standalone, secret-free administrator template from published digests."""
import argparse
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]


def render(app_image, caddy_image):
    for name, image in (('app', app_image), ('caddy', caddy_image)):
        if not re.fullmatch(r'ghcr\.io/denbond007/sopds-nas-' + name + r'@sha256:[0-9a-f]{64}', image):
            raise ValueError('Expected the private SOPDS ' + name + ' image pinned by digest')
    return (ROOT / 'infra/truenas/compose.template.yaml').read_text().replace(
        '__APP_IMAGE__', app_image).replace('__CADDY_IMAGE__', caddy_image).replace(
        '__APP_RELEASE__', app_image.rsplit('@', 1)[1])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-image', required=True)
    parser.add_argument('--caddy-image', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(render(args.app_image, args.caddy_image))


if __name__ == '__main__':
    main()
