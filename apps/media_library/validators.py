"""File validation for media library uploads."""

from django.conf import settings

ALLOWED_MIME_TYPES = {
    "image": [
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
        "image/svg+xml",
    ],
    "video": [
        "video/mp4",
        "video/quicktime",
        "video/x-msvideo",
        "video/webm",
    ],
    "document": [
        "application/pdf",
    ],
}

MIME_TO_FILE_TYPE = {}
for file_type, mimes in ALLOWED_MIME_TYPES.items():
    for mime in mimes:
        MIME_TO_FILE_TYPE[mime] = "gif" if mime == "image/gif" else file_type

ALL_ALLOWED_MIMES = set()
for mimes in ALLOWED_MIME_TYPES.values():
    ALL_ALLOWED_MIMES.update(mimes)

ALLOWED_EXTENSIONS = {
    "image": ["jpg", "jpeg", "png", "webp", "gif", "svg"],
    "video": ["mp4", "mov", "avi", "webm"],
    "document": ["pdf"],
}

ALL_ALLOWED_EXTENSIONS = set()
for exts in ALLOWED_EXTENSIONS.values():
    ALL_ALLOWED_EXTENSIONS.update(exts)

MAX_FILE_SIZES = {
    "image": getattr(settings, "MEDIA_LIBRARY_MAX_IMAGE_SIZE", 20 * 1024 * 1024),
    "gif": getattr(settings, "MEDIA_LIBRARY_MAX_IMAGE_SIZE", 20 * 1024 * 1024),
    "video": getattr(settings, "MEDIA_LIBRARY_MAX_VIDEO_SIZE", 1024 * 1024 * 1024),
    "document": getattr(settings, "MEDIA_LIBRARY_MAX_IMAGE_SIZE", 20 * 1024 * 1024),
}


def determine_file_type(mime_type):
    """Map a MIME type to our FileType enum value."""
    return MIME_TO_FILE_TYPE.get(mime_type)


def validate_file(uploaded_file):
    """Validate an uploaded file. Returns (file_type, errors)."""
    errors = []
    content_type = uploaded_file.content_type or ""
    file_type = determine_file_type(content_type)

    if not file_type:
        errors.append(f"Unsupported file type: {content_type}")
        return None, errors

    max_size = MAX_FILE_SIZES.get(file_type, 20 * 1024 * 1024)
    if uploaded_file.size > max_size:
        max_mb = max_size / (1024 * 1024)
        errors.append(f"File too large. Maximum size for {file_type} files is {max_mb:.0f}MB.")

    return file_type, errors


def get_accepted_file_types():
    """Return a comma-separated string of accepted MIME types for HTML file input."""
    return ",".join(sorted(ALL_ALLOWED_MIMES))
