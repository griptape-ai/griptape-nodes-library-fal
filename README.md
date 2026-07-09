# fal Nodes for Griptape

This library provides Griptape Nodes that call [fal.ai](https://fal.ai/) models directly, bringing fast, hosted AI inference into your Griptape workflows. The current nodes wrap SeedVR2 for high-quality video and image upscaling.

## Features

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
- Upscaling large or long videos can take time. The node polls fal's queue until the job completes.

**Invalid input URL**
- The video and image nodes take a URL as input. Make sure the URL is publicly reachable by fal.ai.

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
