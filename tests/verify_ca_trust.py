"""Verify the image's real aiohttp trust stores before publishing or deploying it."""

import asyncio
import hashlib
import ssl
import sysconfig
from pathlib import Path
from zipfile import ZipFile

import aiohttp
import certifi


COMPROMISED_ROOT = "86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b"
UNTRUSTED_URLS = (
    "https://dv-g3b-emudhra.we-have-a-problem.com/",
    "https://dv-g3b-aaa-emudhra.we-have-a-problem.com/",
)
SERVICE_URLS = (
    ("https://www.youtube.com/generate_204", {204}),
    ("https://www.googleapis.com/discovery/v1/apis/youtube/v3/rest", {200}),
    ("https://sponsor.ajay.app/api/skipSegments?videoID=dQw4w9WgXcQ", {200, 404}),
)
# OpenSSL issuer/self-signed failures only. Expiry and hostname errors do not count.
UNTRUSTED_CHAIN_CODES = {2, 18, 19, 20, 21}


def check_roots(name, context):
    fingerprints = {
        hashlib.sha256(cert).hexdigest() for cert in context.get_ca_certs(binary_form=True)
    }
    if COMPROMISED_ROOT in fingerprints:
        raise AssertionError(f"{name}: compromised emSign ECC Root CA - G3 is still trusted")
    if not fingerprints:
        raise AssertionError(f"{name}: empty CA store")
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise AssertionError(f"{name}: certificate or hostname verification is disabled")
    print(f"PASS {name}: {len(fingerprints)} roots, affected root absent", flush=True)


async def check_url(session, store, url, accepted_statuses=None):
    try:
        async with session.get(url, allow_redirects=False) as response:
            if accepted_statuses is None:
                raise AssertionError(f"{store}: unexpectedly trusted {url}")
            if response.status not in accepted_statuses:
                raise AssertionError(f"{store}: {url} returned HTTP {response.status}")
            print(f"PASS {store}: {url} HTTP {response.status}", flush=True)
    except aiohttp.ClientConnectorCertificateError as exc:
        error = exc.certificate_error
        if (
            accepted_statuses is not None
            or not isinstance(error, ssl.SSLCertVerificationError)
            or error.verify_code not in UNTRUSTED_CHAIN_CODES
        ):
            raise
        print(
            f"PASS {store}: rejected {url}, verify_code={error.verify_code} "
            f"({error.verify_message})",
            flush=True,
        )


async def check_store(name, context=None):
    connector = aiohttp.TCPConnector() if context is None else aiohttp.TCPConnector(ssl=context)
    async with aiohttp.ClientSession(
        connector=connector, timeout=aiohttp.ClientTimeout(total=30)
    ) as session:
        await asyncio.gather(
            *(check_url(session, name, url) for url in UNTRUSTED_URLS),
            *(check_url(session, name, url, statuses) for url, statuses in SERVICE_URLS),
        )


async def main():
    print(f"aiohttp={aiohttp.__version__} certifi={certifi.__version__}", flush=True)
    print(f"System trust: {ssl.get_default_verify_paths()}", flush=True)
    check_roots("system", ssl.create_default_context())
    certifi_context = ssl.create_default_context(cafile=certifi.where())
    # Include any bundled/vendored certifi copies that survive the image build.
    python_library = Path(sysconfig.get_path("stdlib"))
    for bundle in sorted(python_library.rglob("cacert.pem")):
        check_roots(str(bundle), ssl.create_default_context(cafile=str(bundle)))
    for wheel in sorted(python_library.rglob("*.whl")):
        with ZipFile(wheel) as archive:
            for name in archive.namelist():
                if name.endswith("cacert.pem"):
                    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                    context.load_verify_locations(cadata=archive.read(name).decode("ascii"))
                    check_roots(f"{wheel}/{name}", context)
    await check_store("aiohttp-default")
    await check_store("certifi", certifi_context)


if __name__ == "__main__":
    asyncio.run(main())
