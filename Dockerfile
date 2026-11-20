ARG ALPINE_VERSION=3.16
ARG RUBY_VERSION=2.7.8
ARG NODE_VERSION=22
ARG CLAMAV_VERSION=1.4

###############################################################################
# Stage 0: NodeJS and the ClamAV client, taken from their official images
# instead of Alpine 3.16's outdated packages or compiling them ourselves
###############################################################################
FROM node:$NODE_VERSION-alpine AS node

# clamdscan plus its own shared libraries, isolated in /opt/clamav because the official
# image is built on a newer Alpine (OpenSSL 3.4+) than our ruby:$RUBY_VERSION-alpine$ALPINE_VERSION
# runtime. The official image is published for linux/amd64 only, so the hyrax-worker
# target (the default, as the last stage) must be built with --platform linux/amd64.
# Keep the RUN below directly under this FROM: a RUN belongs to the nearest FROM above it.
FROM clamav/clamav:$CLAMAV_VERSION AS clamav

RUN mkdir -p /opt/clamav/bin /opt/clamav/lib && \
    cp /usr/bin/clamdscan /opt/clamav/bin/ && \
    ldd /usr/bin/clamdscan | awk '$3 ~ /^\/usr\/lib\// {print $3}' | xargs -I{} cp -L {} /opt/clamav/lib/

###############################################################################
# Stage 1: Builder – install gems, precompile assets, install FITS & pip deps
###############################################################################
FROM ruby:$RUBY_VERSION-alpine$ALPINE_VERSION AS builder

# Necessary for bundler to properly install some gems
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

ARG RUBYGEMS_VERSION=3.2.34
# mariadb-dev is needed to compile the mysql2 gem. sqlite-libs is a leftover: the sqlite3
# gem is in the development group, which this image doesn't install.
ARG DATABASE_APK_PACKAGE="sqlite-libs mariadb-dev"
# Runtime-only tools (LibreOffice, ImageMagick, Ghostscript, ffmpeg, vim) are installed in
# the runtime stage only
ARG EXTRA_APK_PACKAGES="git tzdata python3"

# NodeJS for asset precompilation. Node itself only needs libstdc++ and
# libgcc_s from /usr/lib; copying all of it also brings the node image's Alpine libraries
# (libcrypto/libssl 3, libz, libapk), which nothing here uses and apk doesn't track.
COPY --link --from=node /usr/lib /usr/lib
COPY --link --from=node /usr/local/share /usr/local/share
COPY --link --from=node /usr/local/lib /usr/local/lib
COPY --link --from=node /usr/local/include /usr/local/include
COPY --link --from=node /usr/local/bin /usr/local/bin

RUN apk update && \
    apk --no-cache upgrade && \
    apk --update --no-cache add build-base \
    bash \
    curl \
    openjdk17 \
    unzip \
    yaml \
    zlib-dev \
    yarn \
    $DATABASE_APK_PACKAGE \
    $EXTRA_APK_PACKAGES && \
    ln -sf /usr/bin/python3 /usr/bin/python && \
    python3 -m ensurepip && \
    pip3 install --no-cache --upgrade pip wheel && \
    ln -sf /usr/bin/pip3 /usr/bin/pip && \
    mkdir /data

WORKDIR /data

# Copy dependency files first for better layer caching
COPY Gemfile Gemfile.lock /data/

ARG RAILS_ENV=production
ENV RAILS_ENV=${RAILS_ENV}

# Update RubyGems system.  RubyGems v3 installs bundler
RUN gem update --system $RUBYGEMS_VERSION \
    && bundle config set --local without 'development test' \
    && bundle install --jobs "$(nproc)" \
    && rm -rf /data/tmp \
    && mkdir /data/tmp

# Copy package.json and yarn.lock for JS dependency caching
COPY package.json yarn.lock /data/
RUN if [ "${RAILS_ENV}" = "production" ]; then \
  RAILS_ENV=$RAILS_ENV SCHOLARS_SECRET_KEY_BASE=temporary bundle exec rails assets:precompile; \
  fi

# Copy requirements.txt separately for pip dependency caching
COPY requirements.txt /data/
RUN pip install --no-cache-dir -r /data/requirements.txt

# Now copy the rest of the source
COPY . /data/

# Workaround for missing work thumbnails (d391a6e): works without a file show the default
# work-*.png by its fingerprinted asset path, which can be stored in Solr when the work is
# indexed. Copy the current image to the fingerprints earlier builds produced so those still resolve.
RUN cp /data/public/assets/work-*.png /data/public/assets/work-ff055336041c3f7d310ad69109eda4a887b16ec501f35afc0a547c4adb97ee72.png && \
    cp /data/public/assets/work-ff055336041c3f7d310ad69109eda4a887b16ec501f35afc0a547c4adb97ee72.png /data/public/assets/work-a6ad224077dcf8d7342d3f671bab54554d5e2e1b9b1506a25411a840b1c85202.png && \
    cp /data/public/assets/work-ff055336041c3f7d310ad69109eda4a887b16ec501f35afc0a547c4adb97ee72.png /data/public/assets/work-e8271462ebe82228c43c3ebe6851235c8919bef84a6360d412615e8e48e38f89.png

# FITS characterizes uploaded files for Hyrax (CharacterizeJob). Its bundled mediainfo
# libraries are built for glibc and don't load on Alpine; the Java-based tools still work.
RUN mkdir -p /opt/fits && \
    curl -fSL -o /opt/fits-1.6.0.zip https://github.com/harvard-lts/fits/releases/download/1.6.0/fits-1.6.0.zip && \
    cd /opt && unzip fits-1.6.0.zip -d /opt/fits && rm fits-1.6.0.zip  && chmod +X /opt/fits/fits.sh

ENV PATH="/opt/fits:${PATH}"

RUN rails hyrax:install:migrations

# Clean up build artifacts in builder to reduce what gets copied
RUN rm -rf /usr/local/bundle/cache/*.gem \
    && find /usr/local/bundle/gems/ -name "*.c" -delete \
    && find /usr/local/bundle/gems/ -name "*.o" -delete \
    && rm -rf /data/tmp/cache \
    && rm -rf /data/app/assets \
    && rm -rf /data/spec \
    && rm -rf /data/solr \
    && rm -rf /data/misc \
    && rm -rf /data/test

###############################################################################
# Stage 2: Runtime – only runtime dependencies, copy artifacts from builders
###############################################################################
FROM ruby:$RUBY_VERSION-alpine$ALPINE_VERSION AS hyrax-base

ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

ARG RAILS_ENV=production
ENV RAILS_ENV=${RAILS_ENV}

# Install only runtime packages (no build-base or compilers)
ARG DATABASE_APK_PACKAGE="mariadb-connector-c-dev"
ARG RUNTIME_APK_PACKAGES="bash curl openjdk17 yaml libreoffice imagemagick ghostscript vim ffmpeg \
  tzdata python3"

# NodeJS in the runtime image too; see the builder stage for what copying /usr/lib brings along
COPY --link --from=node /usr/lib /usr/lib
COPY --link --from=node /usr/local/share /usr/local/share
COPY --link --from=node /usr/local/lib /usr/local/lib
COPY --link --from=node /usr/local/include /usr/local/include
COPY --link --from=node /usr/local/bin /usr/local/bin

# Mountain Time so log timestamps match local time; then remove apk so packages can't be
# installed in the running container
RUN apk update && \
    apk --no-cache upgrade && \
    apk --update --no-cache add \
    $DATABASE_APK_PACKAGE \
    $RUNTIME_APK_PACKAGES && \
    cp /usr/share/zoneinfo/America/Denver /etc/localtime && \
    rm -f /sbin/apk && \
    rm -rf /etc/apk /lib/apk /usr/share/apk /var/lib/apk /usr/mysql-test

# Use --link for better BuildKit layer caching
COPY --link --from=builder /usr/lib/python3.10/site-packages /usr/lib/python3.10/site-packages
COPY --link --from=builder /usr/bin/pip /usr/bin/pip
COPY --link --from=builder /usr/bin/pip3 /usr/bin/pip3
COPY --link --from=builder /usr/bin/python /usr/bin/python

# Copy Ruby gems from builder
COPY --link --from=builder /usr/local/bundle /usr/local/bundle

# Copy application code from builder
COPY --link --from=builder /data /data

# Copy FITS
COPY --link --from=builder /opt/fits /opt/fits

ENV PATH="/opt/fits:${PATH}"

WORKDIR /data

EXPOSE 3000/tcp

###############################################################################
# Stage 3: Final images – web (Puma) and worker (Sidekiq)
###############################################################################
FROM hyrax-base AS hyrax-web

CMD ["bundle", "exec", "puma", "-C", "config/puma/production.rb"]


FROM hyrax-base AS hyrax-worker

# Only the worker scans uploads (IngestJob on Sidekiq), so only it gets the clamdscan client;
# clamd runs in a sidecar container from the official clamav/clamav image and is reached
# through the shared /run/clamav socket. The /usr/local/bin/clamdscan wrapper points
# clamdscan at its own libraries in /opt/clamav/lib for that process only, and the build
# fails here if clamdscan can't load them.
COPY --link --from=clamav /opt/clamav /opt/clamav
RUN mkdir -p /etc/clamav /run/clamav && \
    printf 'LocalSocket /run/clamav/clamd.sock\n' > /etc/clamav/clamd.conf && \
    printf '#!/bin/sh\nLD_LIBRARY_PATH=/opt/clamav/lib exec /opt/clamav/bin/clamdscan "$@"\n' > /usr/local/bin/clamdscan && \
    chmod 755 /usr/local/bin/clamdscan && \
    ! LD_LIBRARY_PATH=/opt/clamav/lib ldd /opt/clamav/bin/clamdscan 2>&1 | grep -qE 'not found|Error relocating' && \
    clamdscan --help > /dev/null

CMD ["bundle", "exec", "sidekiq"]
