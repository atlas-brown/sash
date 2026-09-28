class Asash < Formula
  desc "Static analysis for the Unix shell (runs via Docker)"
  homepage "https://github.com/atlas-brown/sash"
  url "https://github.com/atlas-brown/sash/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "99451efde4d1893b0b79ebd8253eb3df4075c27f70f4d202759f9d2e41cd21fc"
  license "MIT"
  head "https://github.com/atlas-brown/sash.git", branch: "master"

  livecheck do
    url :stable
    strategy :github_latest
  end

  def install
    libexec.install "scripts/asash-docker.sh", "scripts/asash-docker-pull.sh"

    image_tag = build.head? ? "latest" : version.to_s

    (bin/"asash").write <<~SH
      #!/usr/bin/env bash
      export ASASH_IMAGE="${ASASH_IMAGE:-ghcr.io/atlas-brown/sash:#{image_tag}}"
      exec "#{libexec}/asash-docker-pull.sh" "$@"
    SH
  end

  def caveats
    <<~EOS
      SaSh runs inside Docker, so a Docker daemon must be installed and running.

      Install Docker: https://docs.docker.com/get-docker/
      Or with Homebrew: brew install --cask docker

    EOS
  end

  test do
    assert_path_exists libexec/"asash-docker-pull.sh"
    assert_match 'ASASH_IMAGE="${ASASH_IMAGE:-ghcr.io/atlas-brown/sash:',
                 (bin/"asash").read
  end
end
