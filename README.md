# fal Nodes for Griptape

This library provides Griptape Nodes that call [fal.ai](https://fal.ai/) models directly, bringing fast, hosted AI inference into your Griptape workflows. It has one node for each fal category (text-to-image, image-to-video, text-to-speech, image-to-3d, and more), each with a dropdown of every fal model in that category, plus dedicated SeedVR2 nodes for video and image upscaling.

## Features

- **One node per fal category**: 19 nodes named after fal's own categories, covering about 1,350 models across image, video, audio, vision, and 3D.
- **A model dropdown on every node**: Search the list and pick a model. The node's inputs change to match that model.
- **Switching models keeps your work**: Inputs that exist in both models keep their values and connections. Only the inputs that differ are added or removed.
- **Every model option available**: Dropdowns, sliders, and toggles are generated from each model's schema. Hover an input to see fal's description, range, and default.
- **Escape hatch**: Each node has an `extra_payload` JSON input for any request field the node does not show.
- **Video upscaling**: Upscale a video with SeedVR2 via fal.ai, with control over target resolution, output format, quality, and write mode.
- **Image upscaling**: Upscale an image with SeedVR2 via fal.ai, with control over target resolution and output format.
- **Factor or target modes**: Upscale by a multiplier (`factor`) or to a fixed resolution (`target`).
- **Hosted inference**: Runs on fal.ai's infrastructure, so no local GPU is required.

## Installation

1. Clone this repository into your Griptape Nodes workspace directory:

```bash
# Navigate to your workspace directory
# On Mac or Linux you can use the command below to print your workspace directory
cd $(gtn config show | grep workspace_directory | cut -d'"' -f4)
# On Windows, the default workspace directory is a directory named GriptapeNodes in your home directory.
# Usually this is C:\Users\<username>\GriptapeNodes

# Clone the repository
git clone https://github.com/griptape-ai/griptape-nodes-library-fal.git
```

2. Install dependencies:

```bash
cd griptape-nodes-library-fal
uv sync
```

## API Key Setup

You'll need a fal.ai API key to use these nodes.

### Get Your API Key

1. Visit [fal.ai](https://fal.ai/) and sign in to your account.
2. Navigate to your [API keys](https://fal.ai/dashboard/keys) dashboard.
3. Generate a new API key.

### Configure Your API Key

Configure your API key through the Griptape Nodes IDE:

1. Open the **Settings** menu.
2. Navigate to the **API Keys & Secrets** panel.
3. Add your `FAL_KEY` in the respective field.

`FAL_KEY` is the whole key from the fal dashboard, in the form `<key id>:<key secret>`. Paste it as is, without a `Key ` or `Bearer ` prefix.

### Griptape Cloud key for media inputs

Nodes with image, video, or audio inputs upload your file to a short-lived public URL so fal can fetch it. This uses Griptape Cloud storage, so add your `GT_CLOUD_API_KEY` in the same panel. Text-only runs, such as text-to-image from a prompt, do not need it.

## Add your library to your installed Engine!

If you haven't already installed your Griptape Nodes engine, follow the installation steps [HERE](https://github.com/griptape-ai/griptape-nodes).
After you've completed those and you have your engine up and running:

1. Copy the path to your `griptape-nodes-library.json` file. Right click on the file, and `Copy Path` (Not `Copy Relative Path`).
2. Start up the engine!
3. Navigate to settings.
4. Open your settings and go to the App Events tab. Add an item in **Libraries to Register**.
5. Paste your copied `griptape-nodes-library.json` path from earlier into the new item.
6. Exit out of Settings. It will save automatically!
7. Open up the **Libraries** dropdown on the left sidebar.
8. Your newly registered library should appear! Drag and drop nodes to use them!

## Available Nodes

### Category nodes

Each category node lists every fal model in its category. Counts are from the last schema snapshot.

| Node | fal category | Models | Output |
|---|---|---|---|
| FAL Text To Image | `text-to-image` | 187 | Image |
| FAL Image To Image | `image-to-image` | 381 | Image |
| FAL Text To Video | `text-to-video` | 119 | Video |
| FAL Image To Video | `image-to-video` | 183 | Video |
| FAL Video To Video | `video-to-video` | 192 | Video |
| FAL Audio To Video | `audio-to-video` | 18 | Video |
| FAL Text To Audio | `text-to-audio` | 60 | Audio |
| FAL Audio To Audio | `audio-to-audio` | 47 | Audio |
| FAL Text To Speech | `text-to-speech` | 40 | Audio |
| FAL Speech To Speech | `speech-to-speech` | 3 | Audio |
| FAL Video To Audio | `video-to-audio` | 6 | Audio |
| FAL Speech To Text | `speech-to-text` | 11 | Text |
| FAL Audio To Text | `audio-to-text` | 5 | Text |
| FAL Video To Text | `video-to-text` | 4 | Text |
| FAL Image To Text | `image-to-text` | 2 | Text |
| FAL Vision | `vision` | 30 | Text |
| FAL Image To 3D | `image-to-3d` | 39 | 3D model |
| FAL Text To 3D | `text-to-3d` | 14 | 3D model |
| FAL 3D To 3D | `3d-to-3d` | 14 | 3D model |

All category nodes work the same way:

- **Model**: Search the list and pick a model. Hover the field for the model's description and a link to its page on fal.ai. You can also connect a string to this input.
- **Model inputs**: Required fields, media inputs, and common settings such as `prompt`, `aspect_ratio`, `duration`, and `seed` are shown directly. Everything else is in a collapsed **Advanced Settings** group.
- **Request contents**: Only values you change from fal's defaults are sent, so the request stays small and fal applies its own defaults for the rest.
- **Extra Payload**: A JSON input, in Advanced Settings, merged into the request last. Use it for fields the node does not show. Some fields take either a preset or a custom object, such as `image_size`. The node shows the presets, and a custom size goes here, for example `{"image_size": {"width": 1280, "height": 720}}`.
- **Media inputs**: Image, video, and audio inputs, including lists such as `image_urls`, accept artifacts, local files, or public URLs. Anything that is not already public is uploaded for the run and deleted afterward.
- **Output**: The first result is saved to the project and shown on the main output (`image`, `video`, `audio`, or the 3D model). If a model returns several results, all of them are on the `*_list` output as fal's URLs. Text nodes return the model's text. Every node also has the verbatim **Provider Response** from fal.
- **Optional choices**: An optional dropdown with no default shows **(model default)**. Picking it leaves the field out of the request.

### FAL SeedVR Video Upscale

Upscale a video using SeedVR2 via fal.ai (`fal-ai/seedvr/upscale/video`).

- **Video URL**: URL of the video to upscale.
- **Upscale Mode**: `factor` to upscale by a multiplier, or `target` to upscale to a fixed resolution.
- **Upscale Factor**: The upscale factor (default: 2.0). Used in `factor` mode.
- **Target Resolution**: `720p`, `1080p`, `1440p`, or `2160p` (default: `1080p`). Used in `target` mode.
- **Noise Scale**: Noise scale applied during upscaling (default: 0.1).
- **Output Format**: `X264 (.mp4)`, `VP9 (.webm)`, `PRORES4444 (.mov)`, or `GIF (.gif)`.
- **Output Write Mode**: `balanced`, `fast`, or `small`.
- **Output Quality**: `low`, `medium`, `high`, or `maximum`.

Outputs the upscaled **Video** as a URL artifact, plus the verbatim **Provider Response** from fal.

### FAL SeedVR Image Upscale

Upscale an image using SeedVR2 via fal.ai (`fal-ai/seedvr/upscale/image`).

- **Image URL**: URL of the image to upscale.
- **Upscale Mode**: `factor` to upscale by a multiplier, or `target` to upscale to a fixed resolution.
- **Upscale Factor**: The upscale factor (default: 2.0). Used in `factor` mode.
- **Target Resolution**: `720p`, `1080p`, `1440p`, or `2160p` (default: `1080p`). Used in `target` mode.
- **Noise Scale**: Noise scale applied during upscaling (default: 0.1).
- **Output Format**: `png`, `jpg`, or `webp`.

Outputs the upscaled **Image** artifact, plus the verbatim **Provider Response** from fal.

## Example Workflow

### Generate an Image

1. Add a **FAL Text To Image** node.
2. Pick a model from the **Model** dropdown, for example `fal-ai/flux/schnell`.
3. Enter a **Prompt**. Open **Advanced Settings** for the model's other options.
4. Run the workflow. The image is saved to your project and shown on the **Image** output.
5. Change the **Model** to try another one. Your prompt and any matching settings stay in place.

### Upscale a Video

1. Add a **FAL SeedVR Video Upscale** node.
2. Set the **Video URL** to the video you want to upscale.
3. Choose an **Upscale Mode**: set an **Upscale Factor** for `factor` mode, or a **Target Resolution** for `target` mode.
4. Adjust **Output Format**, **Output Quality**, and **Output Write Mode** as needed.
5. Run the workflow. The upscaled video is available on the **Video** output.

## Troubleshooting

**"API key not found"**
- Ensure your `FAL_KEY` secret is configured in the Griptape Nodes IDE Settings → API Keys & Secrets, or set the `FAL_KEY` environment variable.

**Job appears to hang**
- Video, 3D, and large upscale jobs can take several minutes. The node polls fal's queue until the job completes or the **Timeout** is reached. If it times out, the job may still be running on fal. Use the refresh button in the Status group to pull the result onto the node.

**The run fails right away with an HTTP error**
- A 400-level error means fal rejected the request, and the node stops at once and shows fal's message. A 401 or 403 usually means a wrong or revoked `FAL_KEY`. A 422 means a value is out of range or missing. Other errors stop after five failed polls in a row.

**Media upload fails**
- Nodes with image, video, or audio inputs need `GT_CLOUD_API_KEY` to publish the file for fal. See [Griptape Cloud key for media inputs](#griptape-cloud-key-for-media-inputs).

**A model is missing from the dropdown**
- The model list comes from snapshots in `griptape_nodes_library_fal/schemas/`, so models fal added recently are not there yet. See [Refreshing the model list](#refreshing-the-model-list).

**Invalid input URL**
- The SeedVR nodes take a URL as input. Make sure the URL is publicly reachable by fal.ai.

## Refreshing the model list

The category nodes read their models and parameters from JSON snapshots in `griptape_nodes_library_fal/schemas/`, so they load without network access. To pick up new fal models or schema changes, run the fetch script and commit the result:

```bash
# One or more categories
python scripts/fetch_fal_schemas.py text-to-image image-to-video

# Every category
python scripts/fetch_fal_schemas.py --all

# Re-fetch models that are already in a snapshot
python scripts/fetch_fal_schemas.py text-to-image --refresh
```

The script keeps existing entries, so an interrupted run can be resumed. It backs off when fal rate-limits it. A few endpoints, mostly realtime models, have no queue API and are skipped.

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
