FROM python:3.12

WORKDIR /work
RUN apt-get update \
  && apt-get install -y --no-install-recommends openssh-server nodejs npm gdb valgrind build-essential cmake libgtest-dev \
  && npm install --global playwright@1.64.1 \
  && playwright install --with-deps \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Host identity must be generated per container, never shared through the image.
RUN rm -f /etc/ssh/ssh_host_*key*
COPY sshd_config /etc/ssh/sshd_config.harness
COPY start-sshd.sh /usr/local/bin/start-harness-sshd
RUN chmod 755 /usr/local/bin/start-harness-sshd
CMD ["/usr/local/bin/start-harness-sshd"]
