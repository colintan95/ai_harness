# README

## Overview

Harness that runs in a container and uses an agent running on a remote ollama server

## Instructions

Running the harness
- Run `tunnel.sh` on the host and keep it running 
  - This establishes the ssh tunnel to the ollama server
- Run `harness.sh` to start the harness

Building the container image
- Run `build.sh` to create the image
- Make sure to run it when dependencies change
- Main files:
  - `Dockerfile` - setup of the image
  - `requirements.txt` - pip dependencies