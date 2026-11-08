"""Docker storage IDs are not necessarily OCI config IDs.

The caller must first bind the immutable reference/config pair through the
validated CI kit and registry manifest. No tag or unverified pair is accepted.
Keep the identical stdlib-only verifier in the matched driver source.
"""


def pinned_image_id(actual, reference, config_id):
    import re
    if (not isinstance(reference, str)
            or not re.fullmatch(r'ghcr\.io/super-protocol/[a-z0-9][a-z0-9._-]*@sha256:[0-9a-f]{64}', reference)
            or not re.fullmatch(r'sha256:[0-9a-f]{64}', str(config_id))
            or actual.get('Os') != 'linux' or actual.get('Architecture') != 'amd64'
            or reference not in actual.get('RepoDigests', [])):
        raise ValueError('Pinned Linux/amd64 image reference required')
    identity = actual.get('Id')
    if identity == config_id:
        return identity
    # The containerd image store uses the manifest (not config) as Docker ID.
    # Only this exact single-platform descriptor is accepted, never an index.
    descriptor = actual.get('Descriptor', {})
    digest = reference.split('@', 1)[1]
    if (identity == digest and descriptor.get('digest') == digest
            and descriptor.get('mediaType') in (
                'application/vnd.docker.distribution.manifest.v2+json',
                'application/vnd.oci.image.manifest.v1+json')):
        return identity
    raise ValueError('Docker image identity differs from the pinned CI component')
