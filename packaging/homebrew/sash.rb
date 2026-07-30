class SaSh < Formula
  desc "SaSh — static analysis for the Unix shell (runs via Docker)"
  homepage "https://github.com/davidkovach-fuentes/sash"
  version "0.1.1"
  url "https://github.com/davidkovach-fuentes/sash/archive/refs/tags/v#{version}.tar.gz"
  sha256 "1db19b40a26598da6087de20b9a2f9e829085161266211c68201744fc6126ca4"
  license "MIT"

  depends_on "docker"

  def install
    libexec.install "scripts/sash-docker.sh"
    libexec.install "scripts/sash-docker-pull.sh"
    (bin/"sash").write <<~EOS
      #!/bin/bash
      export SASH_IMAGE="${SASH_IMAGE:-ghcr.io/davidkovach-fuentes/sash:#{version}}"
      exec "#{libexec}/sash-docker-pull.sh" "$@"
    EOS
    chmod 0755, bin/"sash"
  end

  def caveats
    <<~EOS
      SaSh needs Docker. Uses ghcr.io/davidkovach-fuentes/sash:#{version} by default
      (pulls it if it is not already local).

        SASH_IMAGE=... sash file.sh
        SASH_RUNTIME=podman sash file.sh

      Build your own image instead:

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
