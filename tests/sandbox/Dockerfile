# A clean machine for bosun (tests/sandbox.sh). It is Linux, because Docker on a Mac is: macos/ holds
# stand-ins for what only macOS has, so it tests bosun's logic and never launchd or a real wake. For
# those, tests/sandbox.sh mac is a real Mac.
FROM python:3.13-slim-bookworm
# hadolint ignore=DL3008 # Debian's current versions: this image is thrown away, and pins would only go stale
RUN apt-get update -qq \
  && apt-get install -y -qq --no-install-recommends tmux jq ruby git curl procps perl zsh >/dev/null \
  && rm -rf /var/lib/apt/lists/* \
  && pip install --quiet --no-cache-dir uv==0.13.6
COPY macos/ /usr/local/bin/
# The mark the scripts here look for: they install bosun for real, so they refuse to run elsewhere.
RUN touch /etc/bosun-sandbox && useradd --create-home --uid 1000 --shell /bin/bash sailor
USER 1000
ENV SHELL=/bin/bash
WORKDIR /home/sailor
