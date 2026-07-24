ARG ALPINE_VERSION=3.16
ARG RUBY_VERSION=2.7.8

###############################################################################
# Stage 1a: ClamAV Builder – compile ClamAV from source (runs in parallel)
###############################################################################
FROM ruby:$RUBY_VERSION-alpine$ALPINE_VERSION AS clamav-builder

ARG CLAMAV_PACKAGES="g++ gcc make cmake python3 bzip2-dev check-dev curl-dev json-c-dev libmilter-dev libxml2-dev \
  linux-headers ncurses-dev openssl libssl3 pcre2-dev zlib-dev musl-dev libunwind-dev gcompat freshclam"

RUN apk update && \
    apk --no-cache upgrade && \
    apk --update --no-cache add \
    bash \
    curl \
    $CLAMAV_PACKAGES

# Install Rust, build ClamAV, then remove Rust toolchain in the same layer
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y && \
    . ~/.cargo/env && rustup toolchain install 1.70.0 && rustup default 1.70.0 && \
    curl -fSL -o ~/clamav-1.0.9.tar.gz https://www.clamav.net/downloads/production/clamav-1.0.9.tar.gz && \
    tar xzf ~/clamav-1.0.9.tar.gz && \
    cd clamav-1.0.9 && mkdir build && cd build && cmake .. \
    -D CMAKE_INSTALL_PREFIX=/usr \
    -D CMAKE_INSTALL_LIBDIR=lib \
    -D APP_CONFIG_DIRECTORY=/etc/clamav \
    -D DATABASE_DIRECTORY=/usr/lib/clamav  \
    -D ENABLE_EXTERNAL_MSPACK=OFF \
    -D ENABLE_CLAMONACC=OFF \
    -D ENABLE_JSON_SHARED=OFF \
    -D JSONC_LIBRARY="/usr/lib/libjson-c.so" && \
    cmake --build . --parallel && cmake --build . --target install && \
    rm -f ~/clamav-1.0.9.tar.gz && \
    rm -rf ~/clamav-1.0.9 && \
    rustup self uninstall -y && \
    rm -rf /root/.cargo /root/.rustup

# Update ClamAV virus database
RUN mkdir /usr/lib/clamav && chmod -R 777 /usr/lib/clamav \
    && chown -R clamav:clamav /usr/lib/clamav/ \
    && freshclam \
    && chmod -R 775 /usr/lib/clamav

###############################################################################
# Stage 1b: Builder – install gems, precompile assets, install FITS & pip deps
###############################################################################
FROM ruby:$RUBY_VERSION-alpine$ALPINE_VERSION AS builder

# Necessary for bundler to properly install some gems
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

ARG RUBYGEMS_VERSION=3.2.34
# Changed sqlite-dev to sqlite-libs (only runtime lib needed for gem linking)
ARG DATABASE_APK_PACKAGE="sqlite-libs mariadb-dev"
# Removed runtime-only packages (libreoffice, imagemagick, ghostscript, vim, ffmpeg, freshclam, clamav-daemon) from builder
# Added python3 to the main apk install to consolidate Python setup
ARG EXTRA_APK_PACKAGES="git tzdata python3"

RUN apk update && \
    apk --no-cache upgrade && \
    apk --update --no-cache add build-base \
    bash \
    curl \
    libxml2-dev \
    nodejs \
    openjdk17 \
    unzip \
    yaml \
    yarn \
    zlib-dev \
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
    && mkdir /data/tmp && mkdir /data/tmp/imports && mkdir /data/tmp/exports

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

RUN cp /data/public/assets/work-*.png /data/public/assets/work-ff055336041c3f7d310ad69109eda4a887b16ec501f35afc0a547c4adb97ee72.png && \
    cp /data/public/assets/work-ff055336041c3f7d310ad69109eda4a887b16ec501f35afc0a547c4adb97ee72.png /data/public/assets/work-a6ad224077dcf8d7342d3f671bab54554d5e2e1b9b1506a25411a840b1c85202.png && \
    cp /data/public/assets/work-ff055336041c3f7d310ad69109eda4a887b16ec501f35afc0a547c4adb97ee72.png /data/public/assets/work-e8271462ebe82228c43c3ebe6851235c8919bef84a6360d412615e8e48e38f89.png


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

# Install only runtime packages (no build-base, compilers, cmake, etc.)
ARG DATABASE_APK_PACKAGE="mariadb-connector-c-dev"
ARG RUNTIME_APK_PACKAGES="bash curl openjdk17 yaml nodejs libreoffice imagemagick ghostscript vim ffmpeg \
  tzdata python3 json-c libmilter libxml2 pcre2 zlib gcompat libunwind ncurses openssl libssl3"

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

RUN addgroup -S clamav && adduser -S -G clamav clamav
# Copy ClamAV binaries and libraries from clamav-builder
COPY --link --from=clamav-builder /usr/bin/clamscan /usr/bin/clamscan
COPY --link --from=clamav-builder /usr/bin/clamdscan /usr/bin/clamdscan
COPY --link --from=clamav-builder /usr/bin/freshclam /usr/bin/freshclam
COPY --link --from=clamav-builder /usr/sbin/clamd /usr/sbin/clamd
COPY --link --from=clamav-builder /usr/lib/libclam* /usr/lib/
COPY --link --from=clamav-builder /usr/lib/libfreshclam* /usr/lib/
COPY --link --from=clamav-builder /etc/clamav /etc/clamav
COPY --link --from=clamav-builder /usr/lib/clamav /usr/lib/clamav
COPY --link --from=clamav-builder /var/log/clamav /var/log/clamav

# Copy FITS
COPY --link --from=builder /opt/fits /opt/fits

ENV PATH="/opt/fits:${PATH}"

WORKDIR /data

EXPOSE 3000/tcp

FROM hyrax-base AS hyrax-web

CMD ["bundle", "exec", "puma", "-C", "config/puma/production.rb"]


FROM hyrax-base AS hyrax-worker

CMD ["bundle", "exec", "sidekiq"]
