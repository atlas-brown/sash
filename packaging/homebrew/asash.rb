class Asash < Formula
  desc "Static analysis for the Unix shell (runs via Docker)"
  homepage "https://github.com/atlas-brown/sash"
  url "https://github.com/atlas-brown/sash/archive/refs/tags/v0.1.1.tar.gz"
  sha256 "ae52254822b387289682f9c5f829e0d87d6ee02bec6e7ab71cab25d5881e8ede"
  license "MIT"
  head "https://github.com/atlas-brown/sash.git", branch: "master"

  livecheck do
    url :stable
    strategy :github_latest
  end

  def install
    libexec.install "scripts/sash-docker.sh", "scripts/sash-docker-pull.sh"

    image_tag = build.head? ? "latest" : version.to_s

    (bin/"sash").write <<~SH
      #!/usr/bin/env bash
      export SASH_IMAGE="${SASH_IMAGE:-ghcr.io/atlas-brown/sash:#{image_tag}}"
      exec "#{libexec}/sash-docker-pull.sh" "$@"
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
    assert_path_exists libexec/"sash-docker-pull.sh"
    assert_match 'SASH_IMAGE="${SASH_IMAGE:-ghcr.io/atlas-brown/sash:',
                 (bin/"sash").read
  end
end
