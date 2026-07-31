class Sash < Formula
  desc 'Static analysis for the Unix shell (runs via Docker)'
  homepage 'https://github.com/atlas-brown/sash'
  url 'https://github.com/atlas-brown/sash/archive/refs/tags/v0.1.1.tar.gz'
  sha256 '1db19b40a26598da6087de20b9a2f9e829085161266211c68201744fc6126ca4'
  license 'MIT'

  depends_on 'docker' => :test

  def install
    libexec.install 'scripts/sash-docker.sh'
    libexec.install 'scripts/sash-docker-pull.sh'
    (bin / 'sash').write <<~EOS
      #!/bin/bash
      export SASH_IMAGE="${SASH_IMAGE:-ghcr.io/atlas-brown/sash:#{version}}"
      exec "#{libexec}/sash-docker-pull.sh" "$@"
    EOS
    chmod 0o755, bin / 'sash'
  end

  def caveats
    <<~EOS
      SaSh requires Docker.

        Install Docker: https://docs.docker.com/get-docker/

    EOS
  end

  test do
    assert_predicate bin / 'sash', :executable?
    assert_match 'docker', shell_output("grep -E 'docker|podman' #{libexec}/sash-docker.sh")
    assert_match(/usage|help|sash/i, shell_output("#{bin}/sash --help"))
  end
end
