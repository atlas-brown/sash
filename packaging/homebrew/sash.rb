class Sash < Formula
  desc "Static analysis tool for the Unix shell (containerized via Docker)"
  homepage "https://github.com/davidkovach-fuentes/sash"
  url "https://github.com/davidkovach-fuentes/sash/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "7beb61e8ae504e036d33f132c75e6c5d379151e469d5eada389cb3006ea043a0"
  version "0.1.0"
  license "MIT"

  depends_on "docker"

  def install
    libexec.install "scripts/sash-docker.sh"
    (bin/"sash").write <<~EOS
      #!/bin/bash
      export SASH_IMAGE="${SASH_IMAGE:-ghcr.io/davidkovach-fuentes/sash:0.1.0}"
      exec "#{libexec}/sash-docker.sh" "$@"
    EOS
  end

  def caveats
    <<~EOS
      sash runs via Docker and pulls ghcr.io/davidkovach-fuentes/sash when needed.

        Override the image:  SASH_IMAGE=... sash file.sh
        Use Podman:          SASH_RUNTIME=podman sash file.sh

      If the image cannot be pulled, build locally from the sash repo:

        docker build --target sys -t sash .
        SASH_IMAGE=sash sash file.sh
    EOS
  end

  test do
    assert_predicate bin/"sash", :executable?
    assert_match "docker", shell_output("grep -E 'docker|podman' #{libexec}/sash-docker.sh")
    assert_match(/usage|help|sash/i, shell_output("#{bin}/sash --help"))
  end
end
