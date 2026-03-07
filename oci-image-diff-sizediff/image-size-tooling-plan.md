# OCI Image Size Tooling Plan

For this problem, split it into two separate needs:

1. What changed between image A and image B?
2. Should this PR be blocked or annotated because the image got too big?

There is not one dominant, polished tool that does both perfectly for OCI images in GitHub PRs. The pragmatic stack is usually:

## Recommended tools

### `dive`

Use `dive` for single-image layer and wasted-space inspection, plus CI thresholds.

- Good for: "did this Dockerfile change add waste?"
- Less good for: "compare two images and show package delta in a PR"
- Supports non-interactive CI mode with `.dive-ci` rules for efficiency and wasted bytes

Sources:

- https://github.com/wagoodman/dive

### `regctl`

Use `regctl` for maintained image-to-image diffs. It can compare manifests, config, and layers.

- Good for: structural OCI diffs between baseline and candidate images
- Best fit here if you want a maintained general-purpose OCI diff tool

Sources:

- https://github.com/regclient/regclient

### `container-diff`

Use `container-diff` if you specifically want package-aware diffs for `apt`, `rpm`, `pip`, `node`, plus file/history/size comparisons between two images.

- Good for: "what package or file differences did this image change introduce?"
- Caveat: useful, but its README says it is in maintenance mode

Sources:

- https://github.com/GoogleContainerTools/container-diff

### `docker scout compare`

Use `docker scout compare` if you already use Docker tooling and want GitHub-friendly compare output.

- Good for: comparing two images and surfacing package and vulnerability changes
- GitHub Action can post a PR comment by default
- More package/security oriented than size oriented

Sources:

- https://docs.docker.com/reference/cli/docker/scout/compare/
- https://docs.docker.com/scout/integrations/ci/gha/

### `docker-image-size-limit`

Use this when you want a hard CI budget gate for built images.

- Good for: "do not let this image exceed X size"
- Supports max size and optional max layers
- Good for enforcement, not analysis

Sources:

- https://github.com/wemake-services/docker-image-size-limit

## Practical workflow

For a repo that builds multiple developer-environment images:

### Local experimentation

Build candidate variants with separate tags, then run:

- `dive` on each candidate for wasted-space analysis
- `regctl` or `container-diff` between baseline and candidate for layer and package deltas

### PR reporting

In GitHub Actions:

1. Build the PR images.
2. Pull the baseline images from `main`.
3. Compute:
   - total image size delta
   - layer diff
   - package diff for the package managers you care about
4. Post a markdown table as a sticky PR comment.

### PR enforcement

Fail the workflow if:

- absolute image size exceeds budget
- size delta exceeds budget
- wasted bytes or layer count regresses too far

## Suggested GitHub stack

For GitHub specifically, the cleanest pattern is usually:

- analysis tool: `regctl` or `docker scout compare`
- budget tool: `docker-image-size-limit`
- comment publishing: `marocchino/sticky-pull-request-comment`

## Opinionated recommendation

If choosing a default stack:

- Best maintained general diff: `regctl`
- Best package-aware diff: `container-diff`, with maintenance-mode caveat
- Best existing GitHub PR experience: `docker scout compare`
- Best simple "don't let images get bigger than X": `docker-image-size-limit`

## What not to rely on alone

`docker buildx du` is useful, but it reports builder cache and disk-usage data rather than a clean per-image PR delta workflow.

Source:

- https://docs.docker.com/reference/cli/docker/buildx/du/

## Proposed next step

If implementing this in CI, the likely workflow is:

1. Build each image target.
2. Compare each one against the corresponding image from `main`.
3. Post a sticky PR comment with a per-image size delta table.
4. Fail only when configured thresholds are crossed.
