- **Breaking:** fal nodes now stop the run when a request fails, instead of ending with only the
  status text set, unless the `Failed` output is wired. The error shows fal's own reason, with the
  status code, request ID and response under More info. A missing or refused `FAL_KEY` links to
  Settings and to fal's key page, and a missing key is reported before any other input problem. The
  library now needs engine 0.104.0 or later.
  [#4](https://github.com/griptape-ai/griptape-nodes-library-fal/issues/4)
