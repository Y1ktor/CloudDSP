"""Size-constrained POST for the editor's exported MIDI snapshot."""
from app.object_storage import ObjectStorageSettings
from app.presigned_upload import (PresignedPost, PresignedUploadContractError, PresignedUploadSigningError,
    DEFAULT_UPLOAD_POST_EXPIRY_SECONDS, MAX_UPLOAD_POST_EXPIRY_SECONDS,
    _normalized_job_id, _validated_positive_limit, _public_s3_client)
from app.sheet_upload_contract import MAX_SHEET_SOURCE_BYTES, SHEET_MEDIA_TYPES

def create_constrained_sheet_upload_post(
    settings: ObjectStorageSettings,
    *,
    job_id: str,
    input_object_key: str,
    content_type: str,
    maximum_source_bytes: int = MAX_SHEET_SOURCE_BYTES,
    expires_in_seconds: int = DEFAULT_UPLOAD_POST_EXPIRY_SECONDS,
) -> PresignedPost:
    """Sign one score source object without widening the audio upload form.

    The exact key, content type, direction metadata, and byte range are S3 POST
    policy conditions enforced by MinIO. A later intake worker must verify the
    object's actual bytes and compare its metadata with the durable score row.
    """

    canonical_job_id = _normalized_job_id(job_id)
    required_prefix = f"midi-sheet-inputs/{canonical_job_id}/"
    extension = input_object_key.removeprefix(required_prefix).removeprefix("source")
    if (
        not input_object_key.startswith(required_prefix)
        or extension != ".mid"
        or input_object_key != f"{required_prefix}source{extension}"
        or content_type != SHEET_MEDIA_TYPES[extension][0]
    ):
        raise PresignedUploadContractError("Score upload key and content type must match one supported source.")
    size_limit = _validated_positive_limit(
        name="maximum_source_bytes", value=maximum_source_bytes, maximum=MAX_SHEET_SOURCE_BYTES
    )
    expiry = _validated_positive_limit(
        name="expires_in_seconds", value=expires_in_seconds, maximum=MAX_UPLOAD_POST_EXPIRY_SECONDS
    )
    try:
        response = _public_s3_client(settings).generate_presigned_post(
            Bucket=settings.uploads_bucket,
            Key=input_object_key,
            Fields={
                "Content-Type": content_type,
                "x-amz-meta-job-id": canonical_job_id,
                "x-amz-meta-score-direction": "midi_to_sheet",
            },
            Conditions=[
                {"Content-Type": content_type},
                {"x-amz-meta-job-id": canonical_job_id},
                {"x-amz-meta-score-direction": "midi_to_sheet"},
                ["content-length-range", 1, size_limit],
            ],
            ExpiresIn=expiry,
        )
    except Exception as error:
        raise PresignedUploadSigningError("S3 SDK could not sign the score upload.") from error
    url, fields = response.get("url"), response.get("fields")
    if not isinstance(url, str) or not url or not isinstance(fields, dict):
        raise PresignedUploadSigningError("S3 SDK returned an incomplete score upload form.")
    return PresignedPost(
        url=url,
        fields={str(name): str(value) for name, value in fields.items()},
        expires_in_seconds=expiry,
        maximum_source_bytes=size_limit,
    )
