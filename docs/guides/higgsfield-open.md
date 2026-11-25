# Higgsfield Open

In **Settings → Models**, choose **Higgsfield Open** for an image or video model. Paste the complete key (`KEY_ID:KEY_SECRET`) from [Higgsfield Open](https://open.higgsfield.ai/api-keys). The provider uses `https://api.higgsfield.ai`.

- **Kling Image 4.0** generates 2K images from a prompt or edits up to four reference images. Edit references must be PNG or JPEG, at least 300 pixels on each side, with an aspect ratio between 2:5 and 5:2.
- **Kling 4.0** generates 720p or 1080p videos of 3–30 seconds. Use a prompt, a first-frame image, or an explicit first/last pair. For frame-based generation, leave the aspect ratio on Auto to follow the supplied image.

OpenDirector accepts PNG or JPEG references up to 10 MB each for this provider. It uploads references using Higgsfield's signed upload flow. Image mentions in the prompt follow the upload order.

After submission, OpenDirector stores the remote request ID. Resuming an accepted task polls that ID. Remote cancellation is available while the request is queued. See the [Higgsfield Open quick start](https://open.higgsfield.ai/quick-start).
