FROM python:3.12

WORKDIR /work
RUN apt-get update \
  && apt-get install -y --no-install-recommends nodejs npm tmux gdb valgrind build-essential cmake libgtest-dev \
  && npm install --global playwright@1.64.1 \
  && playwright install --with-deps \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
