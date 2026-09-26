from django.http import FileResponse


def serve_stored_file(stored_file, filename):
    """Stream a stored file through the calling view, which has already checked
    the caller's access.

    This deliberately never redirects to the storage URL, even when S3 hands
    back a signed one. The frontend fetches files with the bearer token so it
    can render them inline, and a cross-origin redirect to the bucket fails in
    the browser unless the bucket carries a CORS rule for every frontend
    origin. Proxying the bytes keeps the storage layer out of the picture.
    """
    return FileResponse(stored_file.open("rb"), as_attachment=True, filename=filename)
