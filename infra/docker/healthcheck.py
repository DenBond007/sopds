"""Probe the internal HTTP listener without credentials or redirect following."""

import http.client
import os


def main():
    connection = http.client.HTTPConnection("127.0.0.1", 8000, timeout=5)
    try:
        connection.request("GET", "/opds/", headers={
            "Host": os.environ["SOPDS_PRIVATE_HOST"],
            "X-Forwarded-Proto": "https",
        })
        response = connection.getresponse()
        # Authenticated-only OPDS intentionally returns 401 to this probe.
        return 0 if response.status in (200, 401) else 1
    except (OSError, http.client.HTTPException, KeyError, ValueError):
        return 1
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
