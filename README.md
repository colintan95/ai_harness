# README

## Overview

Harness that runs in a container and uses an agent running on a remote ollama server

## Instructions

Running the harness
- Run `prompt.sh` on the host. It configures container DNS when needed, starts the SSH tunnel and container, and presents the prompt.
- Enter Ctrl-D to stop the harness and clean up the tunnel.

Building the container image
- Run `build.sh` to create the image
- Make sure to run it when dependencies change
- Main files:
  - `Dockerfile` - setup of the image
  - `requirements.txt` - pip dependencies
