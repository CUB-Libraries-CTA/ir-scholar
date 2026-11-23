ARG ALPINE_VERSION=3.16
ARG RUBY_VERSION=2.7.8
ARG NODE_VERSION=22
# Keep in sync with the worker sidecar's image in misc/production-deploy.md
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
# Stage 1: Builder – install FITS, gems and JS deps, copy the app, precompile assets
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

# NodeJS and yarn (in /opt) for JS dependencies and asset precompilation. Node itself only
# needs libstdc++ and libgcc_s from /usr/lib; copying all of it also brings the node image's
# Alpine libraries (libcrypto/libssl 3, libz, libapk), which nothing here uses and apk doesn't track.
COPY --link --from=node /usr/lib /usr/lib
COPY --link --from=node /usr/local/share /usr/local/share
COPY --link --from=node /usr/local/lib /usr/local/lib
COPY --link --from=node /usr/local/include /usr/local/include
COPY --link --from=node /usr/local/bin /usr/local/bin
COPY --link --from=node /opt /opt

RUN apk update && \
    apk --no-cache upgrade && \
    apk --update --no-cache add build-base \
    bash \
    curl \
    unzip \
    yaml \
    zlib-dev \
    $DATABASE_APK_PACKAGE \
    $EXTRA_APK_PACKAGES && \
    mkdir /data

# FITS characterizes uploaded files for Hyrax (CharacterizeJob). Downloaded before any app
# files are copied so code changes don't re-download it, and checked against a pinned
# SHA-256 (the release's published MD5 matched when it was pinned). Its bundled mediainfo
# libraries are built for glibc and don't load on Alpine; the Java-based tools still work.
ARG FITS_VERSION=1.6.0
ARG FITS_SHA256=32e436effe7251c5b067ec3f02321d5baf4944b3f0d1010fb8ec42039d9e3b73
RUN curl -fSL -o /tmp/fits.zip \
      https://github.com/harvard-lts/fits/releases/download/${FITS_VERSION}/fits-${FITS_VERSION}.zip && \
    echo "${FITS_SHA256}  /tmp/fits.zip" | sha256sum -c - && \
    mkdir -p /opt/fits && \
    unzip -q /tmp/fits.zip -d /opt/fits && \
    rm /tmp/fits.zip && \
    chmod +x /opt/fits/fits.sh

WORKDIR /data

# Copy dependency files first for better layer caching
COPY Gemfile Gemfile.lock /data/

ARG RAILS_ENV=production
ENV RAILS_ENV=${RAILS_ENV}

# Update RubyGems system (RubyGems v3 installs bundler), then install gems without the
# development and test groups
RUN gem update --system $RUBYGEMS_VERSION \
    && bundle config set --local without 'development test' \
    && bundle install --jobs "$(nproc)" \
    && rm -rf /data/tmp \
    && mkdir /data/tmp

# JS dependencies (cached separately from the source). The postinstall script builds
# public/uv (UniversalViewer) from node_modules and config/uv; node_modules is also an asset
# path for the precompile below.
COPY package.json yarn.lock /data/
COPY config/uv /data/config/uv
# yarn.lock fetches one public GitHub dependency over SSH; fetch it over HTTPS instead
RUN mkdir -p /data/public && \
    git config --global url."https://github.com/".insteadOf "ssh://git@github.com/" && \
    yarn install --frozen-lockfile && \
    yarn cache clean

# Now copy the rest of the source. .dockerignore keeps out locally built public/assets,
# public/uv and node_modules, so the image is built from the repo, not the build machine.
COPY . /data/

# Precompile assets (production only). Rails runs `yarn install` again first, as
# NODE_ENV=production unless set, which skips the devDependency shx that the postinstall
# needs to rebuild public/uv, and ignores the failure; NODE_ENV=development keeps it.
# Then work around missing work thumbnails (d391a6e):
# works without a file show the default work-*.png by its fingerprinted asset path, which can
# be stored in Solr when the work is indexed, so copy the freshly compiled image to the
# fingerprints earlier builds produced. Fails if the precompile produced more than one work-*.png.
RUN if [ "${RAILS_ENV}" = "production" ]; then \
      NODE_ENV=development SCHOLARS_SECRET_KEY_BASE=temporary bundle exec rails assets:precompile && \
      src=$(ls /data/public/assets/work-*.png) && \
      for digest in ff055336041c3f7d310ad69109eda4a887b16ec501f35afc0a547c4adb97ee72 \
                    a6ad224077dcf8d7342d3f671bab54554d5e2e1b9b1506a25411a840b1c85202 \
                    e8271462ebe82228c43c3ebe6851235c8919bef84a6360d412615e8e48e38f89; do \
        dest=/data/public/assets/work-$digest.png; \
        [ "$src" = "$dest" ] || cp "$src" "$dest" || exit 1; \
      done; \
    fi

RUN rails hyrax:install:migrations

# Clean up build artifacts in builder to reduce what gets copied
RUN rm -rf /usr/local/bundle/cache/*.gem \
    && find /usr/local/bundle/gems/ -name "*.c" -delete \
    && find /usr/local/bundle/gems/ -name "*.o" -delete \
    && rm -rf /data/tmp/cache \
    && rm -rf /data/node_modules \
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

# Install only runtime packages (no build-base or compilers). A headless Java runtime is
# enough for FITS. No NodeJS: assets are precompiled and the app boots without it. Python
# still comes in as a dependency of libreoffice-common, but none of the repo's Python
# scripts or their pip packages are installed; they run outside the container.
ARG DATABASE_APK_PACKAGE="mariadb-connector-c-dev"
ARG RUNTIME_APK_PACKAGES="bash curl openjdk17-jre-headless yaml libreoffice imagemagick ghostscript vim ffmpeg \
  tzdata"

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

# Copy Ruby gems from builder (--link for better BuildKit layer caching)
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
