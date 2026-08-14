#!/usr/bin/env bash
set -Eeuo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
version_file="$script_dir/version.py"
image_name=${IMAGE_NAME:-alist-storage-sync}

usage() {
    cat <<'EOF'
Build the local alist_storage_sync Docker image with version and latest tags.

Usage:
  ./build.sh
  IMAGE_NAME=your-name/alist-storage-sync ./build.sh

Tags are read from version.py. For APP_VERSION="v1.13.0", the script creates:
  alist-storage-sync:1.13.0
  alist-storage-sync:latest
EOF
}

if [[ ${1:-} == "-h" || ${1:-} == "--help" ]]; then
    usage
    exit 0
fi

if (($# > 0)); then
    printf 'Error: unsupported argument: %s\n\n' "$1" >&2
    usage >&2
    exit 2
fi

if ! command -v docker >/dev/null 2>&1; then
    printf 'Error: docker is not installed or not available in PATH.\n' >&2
    exit 1
fi

if [[ ! -f $version_file ]]; then
    printf 'Error: version file not found: %s\n' "$version_file" >&2
    exit 1
fi

version=$(sed -nE "s/^[[:space:]]*APP_VERSION[[:space:]]*=[[:space:]]*['\"]v?([^'\"]+)['\"].*/\\1/p" "$version_file" | head -n 1)
if [[ -z $version ]]; then
    printf 'Error: unable to read APP_VERSION from %s.\n' "$version_file" >&2
    exit 1
fi

if [[ -z $image_name || $image_name =~ [[:space:]] ]]; then
    printf 'Error: invalid IMAGE_NAME: %s\n' "$image_name" >&2
    exit 1
fi

if ! docker info >/dev/null 2>&1; then
    printf 'Error: Docker daemon is not running or the current user cannot access it.\n' >&2
    printf 'Start Docker and ensure this user can access the Docker socket, then retry.\n' >&2
    exit 1
fi

version_tag="$image_name:$version"
latest_tag="$image_name:latest"

printf 'Building Docker image from %s\n' "$script_dir"
printf '  version: %s\n' "$version_tag"
printf '  latest:  %s\n' "$latest_tag"

docker build \
    --build-arg "MY_VERSION=$version" \
    --label "org.opencontainers.image.version=$version" \
    --tag "$version_tag" \
    --tag "$latest_tag" \
    "$script_dir"

printf '\nBuild complete.\n'
docker image inspect \
    --format '  {{.Id}}{{range .RepoTags}}  {{.}}{{end}}' \
    "$version_tag"
