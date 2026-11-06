# Optional build cache for deploy/Dockerfile.bifrost.
#
# Drop a Bifrost source tarball here as `bifrost.tar.gz` and the image build
# will use it instead of downloading, which makes rebuilds work offline:
#
#   make seed-bifrost          # or, by hand:
#   curl -fsSL -o deploy/vendor/bifrost.tar.gz \
#     https://codeload.github.com/maximhq/bifrost/tar.gz/refs/tags/transports/v2.4.0
#
# The tarball itself is gitignored: it is ~86 MB and reproducible from the tag.
