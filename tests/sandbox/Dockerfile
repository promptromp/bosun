# A clean machine for bosun (tests/sandbox.sh). It is Linux, because Docker on a Mac is: macos/ holds
# stand-ins for what only macOS has, so it tests bosun's logic and never launchd or a real wake.
FROM python:3.13-slim-bookworm
RUN apt-get update -qq \
  && apt-get install -y -qq --no-install-recommends tmux jq ruby git curl procps perl zsh >/dev/null \
  && pip install --quiet --no-cache-dir uv==0.13.6
COPY macos/ /usr/local/bin/
# The mark the scripts here look for: they install bosun for real, so they refuse to run elsewhere.
RUN touch /etc/bosun-sandbox && useradd --create-home --shell /bin/bash sailor
USER sailor
ENV SHELL=/bin/bash
WORKDIR /home/sailor
